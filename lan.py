"""Second-screen access: the dashboard on a phone or tablet on the same
network.

Off by default. When the user turns it on, the dashboard gets a second
listener on the LAN (config.DASHBOARD_LAN_PORT) that only serves the
read-only views: the page, /state.json and a trimmed /auth/state, plus the
recap button. Login, billing and settings stay on the localhost listener.

A device gets in by entering the 6-digit code shown on this computer (or
opening the URL that carries it). That exchanges the code for a long random
device token in an HttpOnly cookie, which every later request must present.
The code is short enough to type on a phone. After MAX_FAILED_CODES wrong
guesses it is replaced, so guessing it is hopeless. "Unpair all" replaces
the device token, which locks out every paired device at once.
"""
import hmac
import json
import logging
import os
import secrets
import threading
from http.cookies import CookieError, SimpleCookie

import config

log = logging.getLogger("lan")

SETTINGS_PATH = config.APP_DIR / "lan.json"
COOKIE = "pitwall_device"
MAX_FAILED_CODES = 10


def _new_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


class LanAccess:
    def __init__(self, path=SETTINGS_PATH):
        self._path = path
        self._lock = threading.Lock()
        data = self._load()
        self.enabled = bool(data.get("enabled"))
        self._device_token = data.get("deviceToken") or secrets.token_urlsafe(32)
        self._code = _new_code()
        self._failures = 0

    @property
    def code(self) -> str:
        with self._lock:
            return self._code

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self.enabled:
                self._code, self._failures = _new_code(), 0
            self.enabled = enabled
            self._save()

    def unpair_all(self) -> None:
        with self._lock:
            self._device_token = secrets.token_urlsafe(32)
            self._code, self._failures = _new_code(), 0
            self._save()
        log.info("all paired devices removed")

    def device_ok(self, cookie_header: str | None) -> bool:
        """True if the request's cookie carries the current device token."""
        if not cookie_header:
            return False
        try:
            morsel = SimpleCookie(cookie_header).get(COOKIE)
        except CookieError:
            return False
        if morsel is None:
            return False
        with self._lock:
            token = self._device_token
        return hmac.compare_digest(morsel.value.encode(), token.encode())

    def try_pair(self, code) -> bool:
        """Checks a pairing code. Repeated wrong codes replace it."""
        code = str(code or "").strip()
        with self._lock:
            if code and hmac.compare_digest(code.encode(), self._code.encode()):
                self._failures = 0
                return True
            self._failures += 1
            if self._failures >= MAX_FAILED_CODES:
                self._code, self._failures = _new_code(), 0
                log.warning("too many wrong pairing codes — code replaced")
            return False

    def set_cookie_header(self) -> str:
        with self._lock:
            token = self._device_token
        return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age={365 * 86400}")

    # ---- persistence ----
    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text())
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            log.warning("lan.json unreadable — starting with LAN access off")
            return {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps({
                "enabled": self.enabled, "deviceToken": self._device_token}))
            os.chmod(self._path, 0o600)
        except Exception:
            log.exception("could not save lan.json")


PAIR_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PitWall — pair this device</title>
<style>
  body { margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #0A0C10; color: #E8EAF0;
         font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; padding: 16px;
         box-sizing: border-box; }
  form { background: #12151C; border: 1px solid #4A3568; border-radius: 12px;
         padding: 24px; width: min(360px, 100%); display: flex;
         flex-direction: column; gap: 14px; box-sizing: border-box; }
  h1 { margin: 0; font-size: 20px; letter-spacing: 3px; }
  p { margin: 0; color: #9AA3B5; line-height: 1.5; font-size: 15px; }
  input { font: 600 28px ui-monospace, Menlo, monospace; letter-spacing: 8px;
          text-align: center; background: #0A0C10; color: #E8EAF0;
          border: 1px solid #232833; border-radius: 8px; padding: 12px; }
  button { font: 700 16px sans-serif; letter-spacing: 2px; padding: 12px;
           border-radius: 8px; border: 0; background: #9D5CFF; color: #16101F; }
  #msg { color: #FFD24A; min-height: 18px; font-size: 14px; }
</style></head><body>
<form id="f">
  <h1>PIT WALL</h1>
  <p>Enter the 6-digit code shown on the PitWall computer under
     <b>PHONE</b>.</p>
  <input id="code" inputmode="numeric" autocomplete="one-time-code"
         maxlength="6" pattern="[0-9]{6}" required autofocus>
  <button>PAIR THIS DEVICE</button>
  <div id="msg"></div>
</form>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = document.getElementById('msg');
  msg.textContent = 'Pairing…';
  try {
    const res = await fetch('/pair', { method: 'POST',
      body: JSON.stringify({ code: document.getElementById('code').value }) });
    if (res.ok) { location.replace('/'); return; }
    msg.textContent = 'That code is not right. Check the PitWall computer.';
  } catch (err) { msg.textContent = 'Could not reach PitWall.'; }
});
</script></body></html>
"""
