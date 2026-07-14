"""Cloud backend — Supabase auth and subscription entitlement.

License model: ONE check against the server at startup, then hands off.
The verdict is cached to license.json so a machine that's offline (or on
flaky track-side Wi-Fi) keeps working for LICENSE_GRACE_DAYS after its
last successful check. After startup, entitlement only changes on
user-initiated auth actions (login / subscribe / refresh button / logout)
— never on a timer, so a network blip can't cut a session short.
Session data stays local (driver_db.json); nothing is uploaded.

Network calls shell out to `curl` rather than urllib: python.org's macOS
Python builds ship their own OpenSSL with no CA bundle wired up until
"Install Certificates.command" is run, which is a common trap for anyone
who installs Python from python.org rather than Homebrew/system Python —
and this app needs to work out of the box for people who download it, not
just on machines already configured for Python dev. curl uses the OS trust
store and just works.

The browser (dashboard) does the actual email/password login with
supabase-js and hands the tokens to this process, which persists the
refresh token (stay logged in across launches), and checks
profiles.subscription_status. The engine asks one question:
`cloud.entitled`.
"""
import json
import logging
import os
import subprocess
import threading
import time

import config

log = logging.getLogger("cloud")

AUTH_PATH = config.APP_DIR / "auth.json"
LICENSE_PATH = config.APP_DIR / "license.json"
LICENSE_GRACE_DAYS = 7       # offline grace window after the last good check
ENTITLED_STATUSES = {"active"}


class Unreachable(RuntimeError):
    """The license server could not be reached (network problem) — as
    opposed to the server answering and saying no."""


def grace_verdict(cache: dict | None, now: float) -> tuple[bool, float]:
    """(entitled, days_left) from the cached license alone — used when the
    license server is unreachable. Pure function, unit-testable."""
    if not cache or cache.get("status") not in ENTITLED_STATUSES:
        return False, 0.0
    left = LICENSE_GRACE_DAYS - (now - cache.get("checked_at", 0)) / 86400
    return (left > 0), max(0.0, round(left, 1))


class Cloud:
    def __init__(self):
        self.configured = bool(config.SUPABASE_URL and config.SUPABASE_ANON_KEY)
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
        if not self.configured:
            log.warning("no SUPABASE_URL/SUPABASE_ANON_KEY — running in LOCAL "
                        "DEV MODE (no auth, no paywall)")
            return
        # Synchronous local reads so the first telemetry packets aren't
        # dropped while the network check runs: adopt the cached verdict
        # now, refine it from the server in the background.
        self._refresh = self._load_saved_refresh_token()
        cache = self._read_license()
        if self._refresh and cache:
            self.email = cache.get("email")
            self._entitled, self._grace_days_left = grace_verdict(cache, time.time())
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
        """The server answered: this status is authoritative. Cache it."""
        self.status = status
        self._offline = False
        self._on_grace = False
        self._grace_days_left = 0.0
        self._entitled = status in ENTITLED_STATUSES
        if status is not None:
            self._write_license()

    def _apply_offline(self) -> None:
        """Couldn't reach the server: fall back to the cached license."""
        self._offline = True
        self._entitled, self._grace_days_left = grace_verdict(
            self._read_license(), time.time())
        self._on_grace = self._entitled

    # ---- auth lifecycle (user-initiated) ----
    def set_tokens(self, access_token: str, refresh_token: str) -> dict:
        """Called by the dashboard after a browser login."""
        with self._lock:
            self._access = access_token
            self._refresh = refresh_token
        try:
            self._fetch_user()
            self._fetch_profile()
        except Unreachable:
            self._apply_offline()
        except Exception:
            log.exception("could not fetch user/profile after login")
        else:
            self._apply_live(self.status)
        self._persist()
        log.info("logged in as %s (subscription: %s)", self.email, self.status)
        return self.state()

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
                self._fetch_profile()
            except Unreachable:
                # Don't revoke a live-checked entitlement over a blip —
                # just flag that we're offline.
                self._offline = True
            except Exception:
                log.exception("could not re-check subscription status")
            else:
                self._apply_live(self.status)
        return self.state()

    # ---- server calls (raise Unreachable on network failure) ----
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

    @staticmethod
    def _read_license() -> dict | None:
        try:
            return json.loads(LICENSE_PATH.read_text())
        except FileNotFoundError:
            return None
        except Exception:
            log.exception("license.json unreadable")
            return None

    def _write_license(self) -> None:
        try:
            LICENSE_PATH.write_text(json.dumps({
                "email": self.email,
                "user_id": self.user_id,
                "status": self.status,
                "checked_at": time.time(),
            }))
            os.chmod(LICENSE_PATH, 0o600)
        except Exception:
            log.exception("could not write license cache")

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
        Raises Unreachable when the server can't be contacted, RuntimeError
        on any non-2xx status, mirroring urlopen's behaviour."""
        cmd = ["curl", "-s", "-X", method,
               "-w", "\n%{http_code}",
               "-H", f"apikey: {config.SUPABASE_ANON_KEY}",
               "-H", "Content-Type: application/json"]
        if auth:
            with self._lock:
                token = self._access
            if not token:
                raise RuntimeError("not logged in")
            cmd += ["-H", f"Authorization: Bearer {token}"]
        if prefer:
            cmd += ["-H", f"Prefer: {prefer}"]
        if body is not None:
            cmd += ["--data-binary", json.dumps(body)]
        cmd.append(config.SUPABASE_URL + path)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=10)
        except subprocess.TimeoutExpired:
            raise Unreachable("request timed out")
        if result.returncode != 0:
            raise Unreachable(f"curl failed (exit {result.returncode}): "
                              f"{result.stderr.strip()[:200]}")
        raw, _, status = result.stdout.rpartition("\n")
        if not status.isdigit():
            raise Unreachable(f"{path} -> malformed response")
        if int(status) >= 400:
            raise RuntimeError(f"{path} -> HTTP {status}: {raw[:300]}")
        return json.loads(raw) if raw.strip() else None
