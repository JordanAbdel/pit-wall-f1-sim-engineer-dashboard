"""Cloud backend — Supabase auth, subscription entitlement, and session
persistence. Same graceful-fallback pattern as the Recipe app: unconfigured
env -> local dev mode, nothing breaks.

Network calls shell out to `curl` rather than urllib: python.org's macOS
Python builds ship their own OpenSSL with no CA bundle wired up until
"Install Certificates.command" is run, which is a common trap for anyone
who installs Python from python.org rather than Homebrew/system Python —
and this app needs to work out of the box for people who download it, not
just on machines already configured for Python dev. curl uses the OS trust
store and just works.

The browser (dashboard) does the actual email/password login with
supabase-js and hands the tokens to this process, which persists the
refresh token (stay logged in across launches), keeps the access token
fresh, and checks profiles.subscription_status. The engine asks one
question: `cloud.entitled`.
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
STATUS_POLL_S = 600          # re-check subscription status this often
ENTITLED_STATUSES = {"active"}


class Cloud:
    def __init__(self):
        self.configured = bool(config.SUPABASE_URL and config.SUPABASE_ANON_KEY)
        self.email = None
        self.user_id = None
        self.status = None           # profiles.subscription_status
        self._access = None
        self._refresh = None
        self._lock = threading.Lock()
        self._pending_uploads = []   # in-flight upload threads, for flush()
        if not self.configured:
            log.warning("no SUPABASE_URL/SUPABASE_ANON_KEY — running in LOCAL "
                        "DEV MODE (no auth, no paywall, no cloud persistence)")
            return
        threading.Thread(target=self._startup, daemon=True).start()

    # ---- state the engine and dashboard care about ----
    @property
    def entitled(self) -> bool:
        if not self.configured:
            return True  # local dev mode
        return self.status in ENTITLED_STATUSES

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
        }

    # ---- auth lifecycle ----
    def _startup(self) -> None:
        try:
            saved = json.loads(AUTH_PATH.read_text())
            self._refresh = saved.get("refresh_token")
        except FileNotFoundError:
            self._refresh = None
        except Exception:
            log.exception("auth.json unreadable")
        if self._refresh:
            self._do_refresh()
        while True:
            time.sleep(STATUS_POLL_S)
            if self._refresh:
                self._do_refresh()

    def set_tokens(self, access_token: str, refresh_token: str) -> dict:
        """Called by the dashboard after a browser login."""
        with self._lock:
            self._access = access_token
            self._refresh = refresh_token
        self._load_user_and_profile()
        self._persist()
        log.info("logged in as %s (subscription: %s)", self.email, self.status)
        return self.state()

    def logout(self) -> None:
        with self._lock:
            self._access = self._refresh = None
            self.email = self.user_id = self.status = None
        try:
            AUTH_PATH.unlink()
        except FileNotFoundError:
            pass
        log.info("logged out")

    def refresh_entitlement(self) -> dict:
        """On-demand status re-check (dashboard 'refresh status' button)."""
        if self.logged_in:
            self._load_user_and_profile()
        return self.state()

    def _do_refresh(self) -> None:
        try:
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
            self._load_profile()
        except Exception as e:
            log.warning("token refresh failed (%s) — login required", e)
            with self._lock:
                self._access = None
                self.status = None

    def _load_user_and_profile(self) -> None:
        try:
            user = self._req("/auth/v1/user", auth=True)
            self.user_id = user.get("id")
            self.email = user.get("email")
        except Exception:
            log.exception("could not fetch user")
            return
        self._load_profile()

    def _load_profile(self) -> None:
        if not self.user_id:
            return
        try:
            rows = self._req(
                f"/rest/v1/profiles?id=eq.{self.user_id}"
                f"&select=subscription_status,email", auth=True)
            if rows:
                self.status = rows[0]["subscription_status"]
                self.email = rows[0].get("email") or self.email
        except Exception:
            log.exception("could not fetch profile")

    def _persist(self) -> None:
        try:
            AUTH_PATH.write_text(json.dumps({"refresh_token": self._refresh}))
            os.chmod(AUTH_PATH, 0o600)
        except Exception:
            log.exception("could not persist auth token")

    # ---- session persistence ----
    def upload_session(self, record: dict, laps: list) -> None:
        """Persist a finished session + its laps. Fire-and-forget thread so
        the packet loop never blocks on the network — but tracked so
        flush() can wait for it on shutdown. Without that, a daemon thread
        started right before the process exits gets killed mid-request and
        the last session of a run would silently never reach the cloud."""
        if not (self.configured and self.logged_in and self.user_id):
            return
        t = threading.Thread(target=self._upload, args=(record, laps), daemon=True)
        self._pending_uploads.append(t)
        t.start()

    def flush(self, timeout: float = 15.0) -> None:
        """Block until in-flight session uploads finish, or timeout. Call
        this before the process exits."""
        deadline = time.monotonic() + timeout
        for t in self._pending_uploads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("flush timed out with uploads still pending")
                break
            t.join(timeout=remaining)

    def _upload(self, record: dict, laps: list) -> None:
        try:
            bests = [min((l["sectors"][i] for l in laps), default=None)
                     for i in range(3)] if laps else [None] * 3
            session_row = {
                "user_id": self.user_id,
                "track_id": record.get("track_id"),
                "track_name": record.get("track_name"),
                "session_type": record.get("session_type"),
                "laps_count": record.get("laps", len(laps)),
                "best_lap_ms": record.get("best_lap_ms"),
                "best_s1_ms": bests[0], "best_s2_ms": bests[1],
                "best_s3_ms": bests[2],
                "consistency_ms": record.get("consistency_ms"),
                "weakest_sector": record.get("weakest_sector"),
                "recurring": record.get("recurring"),
            }
            created = self._req("/rest/v1/sessions", method="POST",
                                body=[session_row], auth=True,
                                prefer="return=representation")
            session_id = created[0]["id"]
            running_best = None
            lap_rows = []
            for l in laps:
                total = sum(l["sectors"])
                is_pb = (not l.get("invalid", False)
                         and (running_best is None or total < running_best))
                if is_pb:
                    running_best = total
                lap_rows.append({
                    "session_id": session_id, "user_id": self.user_id,
                    "lap_num": l["lap"], "lap_time_ms": total,
                    "s1_ms": l["sectors"][0], "s2_ms": l["sectors"][1],
                    "s3_ms": l["sectors"][2],
                    "invalid": bool(l.get("invalid", False)),
                    "wear_pct": l.get("wear"), "fuel_kg": l.get("fuel"),
                    "yellow": bool(l.get("yellow", False)), "is_pb": is_pb,
                })
            if lap_rows:
                self._req("/rest/v1/laps", method="POST", body=lap_rows,
                          auth=True, prefer="return=minimal")
            log.info("uploaded session (%d laps) to cloud", len(lap_rows))
        except Exception:
            log.exception("session upload failed (kept in local driver_db)")

    # ---- plumbing ----
    def _req(self, path: str, method: str = "GET", body=None,
             auth: bool = False, prefer: str | None = None):
        """Runs the request via `curl` (see module docstring for why) and
        raises on any non-2xx status, mirroring urlopen's behaviour."""
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

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"curl failed (exit {result.returncode}): "
                               f"{result.stderr.strip()}")
        raw, _, status = result.stdout.rpartition("\n")
        if not status.isdigit() or int(status) >= 400:
            raise RuntimeError(f"{path} -> HTTP {status}: {raw[:300]}")
        return json.loads(raw) if raw.strip() else None
