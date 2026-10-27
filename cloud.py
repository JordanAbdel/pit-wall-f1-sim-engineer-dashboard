"""Cloud backend — account sign-in and subscription entitlement, against
the licence API (cloudflare/: a Worker + D1 that speaks the same paths the
old Supabase project did).

License model: ONE check against the server at startup, then hands off.
When the check says the user is entitled, the server also issues an
Ed25519-signed offline licence (supabase/functions/issue-license), cached
in license.json. A machine that's offline (or on flaky track-side Wi-Fi)
keeps working until that licence expires, LICENSE_GRACE_DAYS after it was
issued. The signature is verified against LICENSE_PUBLIC_KEY, which is
baked into the build, so editing license.json can't extend or forge it.
After startup, entitlement only changes on user-initiated auth actions
(login / subscribe / refresh button / logout). It never changes on a
timer, so a network blip can't cut a session short.
Session data stays local (driver_db.json); nothing is uploaded.

Network calls shell out to `curl` rather than urllib: python.org's macOS
Python builds ship their own OpenSSL with no CA bundle wired up until
"Install Certificates.command" is run, which is a common trap for anyone
who installs Python from python.org rather than Homebrew/system Python —
and this app needs to work out of the box for people who download it, not
just on machines already configured for Python dev. curl uses the OS trust
store and just works.

Everything that talks to Supabase or Stripe goes through this process:
email/password login, starting Checkout and opening the Customer Portal.
The dashboard page only calls this app on localhost, so it loads no
third-party auth code and never holds a token. The refresh token persists
in auth.json (stay logged in across launches). The engine asks one
question: `cloud.entitled`.
"""
import base64
import json
import logging
import os
import subprocess
import threading
import time
from urllib.parse import urlparse

import config
import ed25519

log = logging.getLogger("cloud")

AUTH_PATH = config.APP_DIR / "auth.json"
LICENSE_PATH = config.APP_DIR / "license.json"
LICENSE_GRACE_DAYS = 7       # offline grace window after the last good check
LICENSE_CLOCK_SKEW_S = 300   # tolerated clock difference vs. the server
# past_due: Stripe is still retrying the card, so keep the app working
# rather than cutting the driver off over a bank decline.
ENTITLED_STATUSES = {"active", "past_due"}
# Billing links the dashboard may be sent to (Checkout and Customer Portal).
BILLING_HOSTS = ("checkout.stripe.com", "billing.stripe.com")


class Unreachable(RuntimeError):
    """The license server could not be reached (network problem) — as
    opposed to the server answering and saying no."""


class ServerError(RuntimeError):
    """The server answered with a non-2xx status."""

    def __init__(self, path: str, status: int, body):
        self.status = status
        self.body = body
        super().__init__(f"{path} -> HTTP {status}: {str(body)[:300]}")

    @property
    def message(self) -> str | None:
        """The human-readable reason, from Supabase Auth or our functions."""
        if isinstance(self.body, dict):
            for key in ("error_description", "msg", "message", "error"):
                if isinstance(self.body.get(key), str):
                    return self.body[key]
        return None


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def read_license_token(token, public_key: bytes | None) -> dict | None:
    """The payload of a correctly signed licence token, else None."""
    if not isinstance(token, str) or not public_key:
        return None
    try:
        payload_b64, sig_b64 = token.split(".")
        if not ed25519.verify(public_key, payload_b64.encode("ascii"),
                              _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, UnicodeError):   # bad base64/JSON, wrong shape
        return None
    return payload if isinstance(payload, dict) else None


def grace_verdict(licence: dict | None, now: float) -> tuple[bool, float]:
    """(entitled, days_left) from a verified licence payload alone. Used
    when the license server is unreachable. Pure function, unit-testable."""
    if not licence or licence.get("status") not in ENTITLED_STATUSES:
        return False, 0.0
    iat, exp = licence.get("iat"), licence.get("exp")
    if not all(isinstance(t, (int, float)) for t in (iat, exp)):
        return False, 0.0
    # Issued "in the future" means the clock was wound back to stretch the
    # window, so refuse it.
    if iat > now + LICENSE_CLOCK_SKEW_S:
        return False, 0.0
    end = min(exp, iat + LICENSE_GRACE_DAYS * 86400)
    left = (end - now) / 86400
    return (left > 0), max(0.0, round(left, 1))


def _load_public_key() -> bytes | None:
    if not config.LICENSE_PUBLIC_KEY:
        return None
    try:
        key = base64.b64decode(config.LICENSE_PUBLIC_KEY, validate=True)
    except ValueError:
        key = b""
    if len(key) != 32:
        log.error("LICENSE_PUBLIC_KEY is not a base64 32-byte Ed25519 key — "
                  "offline licences disabled")
        return None
    return key


class Cloud:
    def __init__(self):
        self.configured = bool(config.API_URL)
        self.email = None
        self.user_id = None
        self.status = None           # profiles.subscription_status
        self._access = None
        self._refresh = None
        self._lock = threading.Lock()
        self._entitled = False
        self._offline = False        # last check couldn't reach the server
        self._on_grace = False
        self._grace_days_left = 0.0
        self._public_key = _load_public_key()
        if not self.configured:
            log.warning("no PITWALL_API_URL — running in LOCAL "
                        "DEV MODE (no auth, no paywall)")
            return
        if not self._public_key:
            log.warning("no LICENSE_PUBLIC_KEY — no offline grace; the licence "
                        "server must be reachable at startup")
        # Synchronous local reads so the first telemetry packets aren't
        # dropped while the network check runs: adopt the cached verdict
        # now, refine it from the server in the background.
        self._refresh = self._load_saved_refresh_token()
        licence = self._read_license()
        if self._refresh and licence:
            self.email = licence.get("email")
            self._entitled, self._grace_days_left = grace_verdict(licence, time.time())
            self._on_grace = self._entitled
        threading.Thread(target=self._check_license, daemon=True).start()

    # ---- state the engine and dashboard care about ----
    @property
    def entitled(self) -> bool:
        if not self.configured:
            return True  # local dev mode
        return self._entitled

    @property
    def logged_in(self) -> bool:
        return self._access is not None

    def state(self) -> dict:
        return {
            "configured": self.configured,
            "devMode": not self.configured,
            "loggedIn": self.logged_in,
            "email": self.email,
            "status": self.status,
            "entitled": self.entitled,
            "offline": self._offline,
            "onGrace": self._on_grace,
            "graceDaysLeft": self._grace_days_left,
        }

    # ---- the startup license check ----
    def _check_license(self) -> None:
        if not self._refresh:
            self._apply_live(None)   # nobody signed in — license required
            log.info("no saved login — sign in via the dashboard")
            return
        try:
            self._do_refresh()
            self._fetch_user()
            self._fetch_profile()
        except Unreachable as e:
            self._apply_offline()
            log.warning("license server unreachable (%s) — %s", e,
                        f"offline grace, {self._grace_days_left} days left"
                        if self._entitled else "no valid cached license")
            return
        except Exception as e:
            with self._lock:
                self._access = None
            self._apply_live(None)
            log.warning("saved login rejected (%s) — sign in via the dashboard", e)
            return
        self._apply_live(self.status)
        log.info("license check: %s (%s)", self.status, self.email)

    def _apply_live(self, status) -> None:
        """The server answered: this status is authoritative. Refresh the
        signed offline licence, or drop it if no longer entitled."""
        self.status = status
        self._offline = False
        self._on_grace = False
        self._grace_days_left = 0.0
        self._entitled = status in ENTITLED_STATUSES
        if self._entitled:
            self._store_license()
        else:
            self._clear_license()

    def _apply_offline(self) -> None:
        """Couldn't reach the server: fall back to the cached licence."""
        self._offline = True
        self._entitled, self._grace_days_left = grace_verdict(
            self._read_license(), time.time())
        self._on_grace = self._entitled

    # ---- auth lifecycle (user-initiated) ----
    def login(self, email: str, password: str, signup: bool = False) -> dict:
        """Email/password log in (or sign up) against Supabase Auth.
        Returns {"ok": bool, "message": str | None}; on success the
        refresh token is persisted and entitlement re-checked."""
        path = ("/auth/v1/signup" if signup
                else "/auth/v1/token?grant_type=password")
        try:
            data = self._req(path, method="POST",
                             body={"email": email, "password": password})
        except Unreachable:
            return {"ok": False, "message": "Can't reach the licence server — "
                    "check your internet connection."}
        except ServerError as e:
            return {"ok": False, "message": e.message or "Login failed."}
        if not data or not data.get("access_token"):
            # Sign-up with email confirmation on: no session until confirmed.
            return {"ok": False, "message": "Check your email to confirm the "
                    "account, then log in."}
        with self._lock:
            self._access = data["access_token"]
            self._refresh = data.get("refresh_token")
        user = data.get("user") or {}
        self.user_id = user.get("id")
        self.email = user.get("email") or email
        self._persist()
        try:
            self._fetch_profile()
        except Unreachable:
            self._apply_offline()
        except Exception:
            log.exception("could not fetch profile after login")
        else:
            self._apply_live(self.status)
        log.info("logged in as %s (subscription: %s)", self.email, self.status)
        return {"ok": True, "message": None}

    def logout(self) -> None:
        with self._lock:
            self._access = self._refresh = None
            self.email = self.user_id = self.status = None
        self._entitled = False
        self._on_grace = False
        self._grace_days_left = 0.0
        for path in (AUTH_PATH, LICENSE_PATH):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        log.info("logged out")

    def refresh_entitlement(self) -> dict:
        """On-demand status re-check (dashboard 'refresh status' button and
        the post-checkout activation chase)."""
        if self.logged_in:
            try:
                self._authed(self._fetch_profile)
            except Unreachable:
                # Don't revoke a live-checked entitlement over a blip —
                # just flag that we're offline.
                self._offline = True
            except Exception:
                log.exception("could not re-check subscription status")
            else:
                self._apply_live(self.status)
        return self.state()

    # ---- billing links (Stripe Checkout / Customer Portal) ----
    def checkout_url(self) -> dict:
        """{"url"} for Stripe Checkout, or {"error"[, "manage"]}. The
        server refuses when a subscription already exists and sets
        `manage` so the dashboard can offer the portal instead."""
        return self._billing_link("create-checkout")

    def portal_url(self) -> dict:
        """{"url"} for the Stripe Customer Portal (cancel, change card,
        invoices), or {"error"}."""
        return self._billing_link("create-portal-session")

    def _billing_link(self, function: str) -> dict:
        if not self.logged_in:
            return {"error": "Log in first (if you're offline, reconnect "
                             "and restart PitWall)."}
        try:
            data = self._authed(lambda: self._call_function(function))
        except Unreachable:
            return {"error": "Can't reach the server — check your internet "
                             "connection."}
        except ServerError as e:
            out = {"error": e.message or "Something went wrong — try again."}
            if isinstance(e.body, dict) and e.body.get("manage"):
                out["manage"] = True
            return out
        except Exception:
            log.exception("%s failed", function)
            return {"error": "Something went wrong — try again."}
        url = (data or {}).get("url") or ""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in BILLING_HOSTS:
            log.error("%s returned an unexpected URL: %r", function, url[:120])
            return {"error": "Something went wrong — try again."}
        return {"url": url}

    # ---- server calls (raise Unreachable on network failure) ----
    def _authed(self, call):
        """Run `call`; if the access token has expired (they last an hour
        and the app runs for longer), refresh it once and retry."""
        try:
            return call()
        except ServerError as e:
            if e.status != 401 or not self._refresh:
                raise
        self._do_refresh()
        return call()

    def _call_function(self, name: str):
        return self._req(f"/functions/v1/{name}", method="POST", body={},
                         auth=True)

    def _do_refresh(self) -> None:
        data = self._req(
            "/auth/v1/token?grant_type=refresh_token", method="POST",
            body={"refresh_token": self._refresh})
        with self._lock:
            self._access = data["access_token"]
            self._refresh = data.get("refresh_token", self._refresh)
        user = data.get("user") or {}
        self.user_id = user.get("id") or self.user_id
        self.email = user.get("email") or self.email
        self._persist()

    def _fetch_user(self) -> None:
        user = self._req("/auth/v1/user", auth=True)
        self.user_id = user.get("id")
        self.email = user.get("email")

    def _fetch_profile(self) -> None:
        if not self.user_id:
            return
        rows = self._req(
            f"/rest/v1/profiles?id=eq.{self.user_id}"
            f"&select=subscription_status,email", auth=True)
        if rows:
            self.status = rows[0]["subscription_status"]
            self.email = rows[0].get("email") or self.email

    # ---- local persistence ----
    @staticmethod
    def _load_saved_refresh_token():
        try:
            return json.loads(AUTH_PATH.read_text()).get("refresh_token")
        except FileNotFoundError:
            return None
        except Exception:
            log.exception("auth.json unreadable")
            return None

    def _read_license(self) -> dict | None:
        """The cached licence's payload, only if its signature verifies."""
        try:
            token = json.loads(LICENSE_PATH.read_text()).get("token")
        except FileNotFoundError:
            return None
        except Exception:
            log.warning("license.json unreadable — ignoring it")
            return None
        licence = read_license_token(token, self._public_key)
        if token and licence is None:
            log.warning("license.json signature invalid — ignoring it")
        return licence

    def _store_license(self) -> None:
        """Fetch a fresh signed licence and cache it for offline starts.
        Failure only costs offline grace; the live verdict stands."""
        if not self._public_key:
            return
        try:
            data = self._authed(lambda: self._call_function("issue-license"))
            token = (data or {}).get("token")
            if read_license_token(token, self._public_key) is None:
                log.error("licence from the server failed verification — "
                          "does LICENSE_PUBLIC_KEY match the server's key?")
                return
            LICENSE_PATH.write_text(json.dumps({"token": token}))
            os.chmod(LICENSE_PATH, 0o600)
        except Exception as e:
            log.warning("could not cache an offline licence (%s) — the next "
                        "start will need the licence server", e)

    @staticmethod
    def _clear_license() -> None:
        try:
            LICENSE_PATH.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.exception("could not remove license.json")

    def _persist(self) -> None:
        try:
            AUTH_PATH.write_text(json.dumps({"refresh_token": self._refresh}))
            os.chmod(AUTH_PATH, 0o600)
        except Exception:
            log.exception("could not persist auth token")

    # ---- plumbing ----
    def _req(self, path: str, method: str = "GET", body=None,
             auth: bool = False, prefer: str | None = None):
        """Runs the request via `curl` (see module docstring for why).
        Raises Unreachable when the server can't be contacted, ServerError
        on any non-2xx status."""
        cmd = ["curl", "-s", "-X", method,
               "-w", "\n%{http_code}",
               "-H", "Content-Type: application/json"]
        if config.SUPABASE_ANON_KEY:   # only the old Supabase backend needs it
            cmd += ["-H", f"apikey: {config.SUPABASE_ANON_KEY}"]
        if auth:
            with self._lock:
                token = self._access
            if not token:
                raise RuntimeError("not logged in")
            cmd += ["-H", f"Authorization: Bearer {token}"]
        if prefer:
            cmd += ["-H", f"Prefer: {prefer}"]
        if body is not None:
            # Via stdin, so passwords never appear in the process list.
            cmd += ["--data-binary", "@-"]
        cmd.append(config.API_URL + path)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
                input=json.dumps(body) if body is not None else None)
        except subprocess.TimeoutExpired:
            raise Unreachable("request timed out")
        if result.returncode != 0:
            raise Unreachable(f"curl failed (exit {result.returncode}): "
                              f"{result.stderr.strip()[:200]}")
        raw, _, status = result.stdout.rpartition("\n")
        if not status.isdigit():
            raise Unreachable(f"{path} -> malformed response")
        try:
            parsed = json.loads(raw) if raw.strip() else None
        except ValueError:
            parsed = raw
        if int(status) >= 400:
            raise ServerError(path, int(status), parsed)
        return parsed
