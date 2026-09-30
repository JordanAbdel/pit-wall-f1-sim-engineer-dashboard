"""Live pit-wall dashboard — stdlib HTTP server, no dependencies.

Serves dashboard.html at / and a JSON state snapshot at /state.json.
The page polls the snapshot a few times a second and derives everything
visual (bests, deltas, pace colours, trend) client-side, mirroring the
Claude Design prototype's logic. The UDP thread writes state through
Dashboard.update()/add_lap(); HTTP threads only read.
"""
import json
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import config
import lan

log = logging.getLogger("dashboard")

HTML_PATH = config.BUNDLE_DIR / "dashboard.html"
LIVE_TIMEOUT_S = 2.0
MAX_BODY_BYTES = 16 * 1024

# The page loads no third-party script (login and billing go through this
# process), so it can pin scripts and requests to its own origin.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")


def lan_ip() -> str:
    """This machine's LAN address — what the console's UDP IP setting must
    be. The connect() never sends a packet; it just picks the route."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return ""


class Dashboard:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_packet = 0.0
        self._data = {
            "sessionLabel": "F1 25",
            "lapNo": None,
            "currentLapMs": 0,
            "currentSector": 0,
            "s1Ms": 0,
            "s2Ms": 0,
            "laps": [],            # [{"lap": n, "sectors": [ms, ms, ms]}, ...]
            "compound": None,
            "tyreAge": None,
            "wearPct": None,
            "fuelKg": None,
            "fuelLaps": None,
            "paused": False,
            "signalFormat": None,  # UDP format of the last packet (e.g. 2024)
            "signalGame": None,    # "F1 24", or None if unsupported
            "spectating": False,   # no player car: coaching paused
            "trackId": None,
            "advice": None,        # imperative coaching line (centrepiece)
            "sectorStories": {},   # per-sector lost/gained corner bullets
            "profile": [],         # driver-style chips
            "recurring": None,     # most repeated mistake, for the recap
            "weakTurns": [],       # session's worst corners, for map + recap
            "trackLength": 0,      # metres, for map position mapping
            "trackPath": None,     # outline recorded from real driving
            "lapDistanceM": 0,     # live car position along the lap
            "reference": {"source": "self", "lapMs": None, "sectors": None,
                          "goalMs": None},
        }
        self._server = None
        self.on_recap = None  # set by the engineer; called from HTTP threads
        self.cloud = None     # set by main; auth/entitlement backend
        self.lan = lan.LanAccess()
        self._lan_server = None
        self._lan_error = None
        self._lan_port = config.DASHBOARD_LAN_PORT
        self._handler_for = None  # set by start(): local? -> handler class

    def touch(self, fmt: int | None = None, game: str | None = None) -> None:
        """A telemetry packet arrived. Called even before the paywall so a
        prospective buyer can see their console connects."""
        with self._lock:
            self._last_packet = time.time()
            if fmt is not None:
                self._data["signalFormat"] = fmt
                self._data["signalGame"] = game

    def update(self, **fields) -> None:
        with self._lock:
            self._data.update(fields)

    def add_lap(self, lap_num: int, s1_ms: int, s2_ms: int, s3_ms: int,
                meta: dict | None = None) -> None:
        entry = {"lap": lap_num, "sectors": [s1_ms, s2_ms, s3_ms],
                 **(meta or {})}
        with self._lock:
            laps = self._data["laps"]
            if laps and laps[-1]["lap"] == lap_num:
                laps[-1] = entry  # backfill already recorded this lap
            else:
                laps.append(entry)

    def set_laps(self, laps: list) -> None:
        with self._lock:
            self._data["laps"] = laps

    def lap_count(self) -> int:
        with self._lock:
            return len(self._data["laps"])

    def snapshot(self) -> dict:
        with self._lock:
            snap = dict(self._data)
            snap["laps"] = list(self._data["laps"])
            snap["live"] = (time.time() - self._last_packet < LIVE_TIMEOUT_S
                            and not self._data["paused"])
            snap["hasSignal"] = time.time() - self._last_packet < LIVE_TIMEOUT_S
        return snap

    def _auth_state(self) -> dict:
        return (self.cloud.state() if self.cloud
                else {"configured": False, "devMode": True,
                      "entitled": True, "loggedIn": False})

    # ---- phone / tablet access (see lan.py) ----
    def lan_state(self) -> dict:
        ip = lan_ip()
        running = self._lan_server is not None
        base = f"http://{ip}:{self._lan_port}/" if ip else None
        code = self.lan.code
        return {
            "enabled": self.lan.enabled,
            "running": running,
            "error": self._lan_error,
            "url": base,
            "code": code if running else None,
            "pairUrl": f"{base}?code={code}" if base and running else None,
        }

    def set_lan(self, enabled: bool) -> dict:
        self.lan.set_enabled(enabled)
        if enabled:
            self._start_lan()
        else:
            self._stop_lan()
        return self.lan_state()

    def _start_lan(self) -> None:
        if self._lan_server is not None or self._handler_for is None:
            return
        try:
            server = ThreadingHTTPServer(("0.0.0.0", self._lan_port),
                                         self._handler_for(False))
        except OSError as e:
            self._lan_error = (f"Port {self._lan_port} is in use by another "
                               "program." if e.errno in (48, 98, 10048)
                               else f"Could not open the network port ({e}).")
            log.warning("LAN dashboard failed to start: %s", e)
            return
        self._lan_error = None
        self._lan_server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("LAN dashboard on port %d (pairing required)", self._lan_port)

    def _stop_lan(self) -> None:
        server, self._lan_server = self._lan_server, None
        self._lan_error = None
        if server is not None:
            server.shutdown()
            server.server_close()
            log.info("LAN dashboard stopped")

    def start(self, port: int) -> None:
        dashboard = self
        # The localhost listener: only the dashboard's own origin may talk
        # to it. That blocks cross-site request forgery from pages the user
        # has open (a page must not be able to log in or open billing) and
        # DNS-rebinding tricks (the Host header must be ours).
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        allowed_origins = {f"http://{h}" for h in allowed_hosts}
        rejected_once = set()
        _lan_ip = lan_ip()  # once — it's shown in the setup guide

        def make_handler(local: bool):
            """local=True: the full app on 127.0.0.1. local=False: the
            paired-device view on the LAN (read-only + recap)."""

            class Handler(BaseHTTPRequestHandler):
                def _reject(self, code=403):
                    self._send(code, "text/plain", b"forbidden")

                def _request_ok(self) -> bool:
                    host = self.headers.get("Host", "")
                    origin = self.headers.get("Origin")
                    if local:
                        ok = host in allowed_hosts and (
                            origin is None or origin in allowed_origins)
                    else:
                        # Any address can reach us on the LAN, so the device
                        # cookie is the real gate. Cross-origin writes are
                        # still refused.
                        ok = origin is None or origin == f"http://{host}"
                    if not ok:
                        key = (host, origin)
                        if key not in rejected_once:
                            rejected_once.add(key)
                            log.warning("rejected request with Host=%r Origin=%r",
                                        host, origin)
                        self._reject()
                    return ok

                def _paired(self) -> bool:
                    return local or dashboard.lan.device_ok(
                        self.headers.get("Cookie"))

                def _json_body(self):
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                    except ValueError:
                        return None
                    if not 0 < length <= MAX_BODY_BYTES:
                        return None
                    try:
                        body = json.loads(self.rfile.read(length))
                    except ValueError:
                        return None
                    return body if isinstance(body, dict) else None

                def _json(self, obj, code=200, headers=None):
                    self._send(code, "application/json",
                               json.dumps(obj).encode(), headers)

                def do_GET(self):
                    if not self._request_ok():
                        return
                    path, _, query = self.path.partition("?")
                    if not local and path == "/" and not self._paired():
                        code = parse_qs(query).get("code", [""])[0]
                        if code and dashboard.lan.try_pair(code):
                            # Drop the code from the address bar.
                            self._send(303, "text/plain", b"", {
                                "Location": "/",
                                "Set-Cookie": dashboard.lan.set_cookie_header()})
                        else:
                            self._send(200, "text/html; charset=utf-8",
                                       lan.PAIR_PAGE.encode(),
                                       {"Content-Security-Policy": CSP})
                        return
                    if not self._paired():
                        return self._reject()
                    if path in ("/", "/index.html"):
                        self._send(200, "text/html; charset=utf-8",
                                   HTML_PATH.read_bytes(), {"Content-Security-Policy": CSP})
                    elif path == "/state.json":
                        self._json(dashboard.snapshot())
                    elif path == "/config.json":
                        self._json({"lanIp": _lan_ip, "udpPort": config.PORT,
                                    "remote": not local})
                    elif path == "/auth/state":
                        state = dashboard._auth_state()
                        if not local:
                            # Account details stay on the PitWall computer.
                            state = {k: state.get(k) for k in
                                     ("configured", "devMode", "entitled")}
                            state["remote"] = True
                        self._json(state)
                    elif path == "/lan" and local:
                        self._json(dashboard.lan_state())
                    else:
                        self._send(404, "text/plain", b"not found")

                def do_POST(self):
                    if not self._request_ok():
                        return
                    path = self.path
                    if path == "/pair" and not local:
                        body = self._json_body() or {}
                        if dashboard.lan.try_pair(body.get("code")):
                            self._send(204, "text/plain", b"", {
                                "Set-Cookie": dashboard.lan.set_cookie_header()})
                        else:
                            self._reject()
                        return
                    if not self._paired():
                        return self._reject()
                    if path == "/recap":
                        if dashboard.on_recap is not None:
                            try:
                                dashboard.on_recap()
                            except Exception:
                                log.exception("recap callback failed")
                        self._send(204, "text/plain", b"")
                        return
                    if not local:
                        return self._reject()
                    cloud = dashboard.cloud
                    if path == "/auth/login" and cloud:
                        body = self._json_body()
                        if not body or not isinstance(body.get("email"), str) \
                                or not isinstance(body.get("password"), str):
                            self._json({"ok": False, "message": "Email and "
                                        "password required."}, 400)
                            return
                        self._json(cloud.login(body["email"].strip(),
                                               body["password"],
                                               bool(body.get("signup"))))
                    elif path == "/auth/refresh" and cloud:
                        self._json(cloud.refresh_entitlement())
                    elif path == "/auth/logout" and cloud:
                        cloud.logout()
                        self._send(204, "text/plain", b"")
                    elif path == "/billing/checkout" and cloud:
                        self._json(cloud.checkout_url())
                    elif path == "/billing/portal" and cloud:
                        self._json(cloud.portal_url())
                    elif path == "/lan":
                        body = self._json_body() or {}
                        self._json(dashboard.set_lan(bool(body.get("enabled"))))
                    elif path == "/lan/unpair":
                        dashboard.lan.unpair_all()
                        self._json(dashboard.lan_state())
                    else:
                        self._send(404, "text/plain", b"not found")

                def _send(self, code, ctype, body, headers=None):
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Frame-Options", "DENY")
                    self.send_header("Referrer-Policy", "no-referrer")
                    for k, v in (headers or {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, fmt, *args):
                    pass

            return Handler

        self._handler_for = make_handler
        self._server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(True))
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        log.info("dashboard at http://127.0.0.1:%d", port)
        if self.lan.enabled:
            self._start_lan()

    def stop(self) -> None:
        self._stop_lan()
        if self._server is not None:
            self._server.shutdown()
