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

import config

log = logging.getLogger("dashboard")

HTML_PATH = config.BUNDLE_DIR / "dashboard.html"
LIVE_TIMEOUT_S = 2.0


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
            "trackId": None,
            "advice": None,        # imperative coaching line (centrepiece)
            "sectorStories": {},   # per-sector lost/gained corner bullets
            "profile": [],         # driver-style chips
            "recurring": None,     # most repeated mistake, for the recap
            "weakTurns": [],       # session's worst corners, for map + recap
            "trackLength": 0,      # metres, for map position mapping
            "lapDistanceM": 0,     # live car position along the lap
            "reference": {"source": "self", "lapMs": None, "sectors": None},
        }
        self._server = None
        self.on_recap = None  # set by the engineer; called from HTTP threads
        self.cloud = None     # set by main; auth/entitlement backend

    def touch(self) -> None:
        with self._lock:
            self._last_packet = time.time()

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

    def start(self, port: int) -> None:
        dashboard = self
        # Only the dashboard's own origin may talk to this server. Blocks
        # cross-site request forgery from web pages the user has open (a
        # page can't be allowed to POST attacker tokens to /auth/session)
        # and DNS-rebinding tricks (Host header must be ours).
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        allowed_origins = {f"http://{h}" for h in allowed_hosts}
        rejected_once = set()
        _lan_ip = lan_ip()  # once — it's shown in the setup guide

        class Handler(BaseHTTPRequestHandler):
            def _request_ok(self) -> bool:
                host = self.headers.get("Host", "")
                origin = self.headers.get("Origin")
                ok = host in allowed_hosts and (origin is None
                                                or origin in allowed_origins)
                if not ok:
                    key = (host, origin)
                    if key not in rejected_once:
                        rejected_once.add(key)
                        log.warning("rejected request with Host=%r Origin=%r",
                                    host, origin)
                    self._send(403, "text/plain", b"forbidden")
                return ok

            def do_GET(self):
                if not self._request_ok():
                    return
                path = self.path.split("?", 1)[0]  # strip query string (e.g. ?billing=success)
                self.path = path
                if self.path in ("/", "/index.html"):
                    body = HTML_PATH.read_bytes()
                    self._send(200, "text/html; charset=utf-8", body)
                elif self.path == "/state.json":
                    body = json.dumps(dashboard.snapshot()).encode()
                    self._send(200, "application/json", body)
                elif self.path == "/config.json":
                    body = json.dumps({
                        "supabaseUrl": config.SUPABASE_URL,
                        "supabaseAnonKey": config.SUPABASE_ANON_KEY,
                        "checkoutUrl": config.CHECKOUT_FUNCTION_URL,
                        "lanIp": _lan_ip,
                        "udpPort": config.PORT,
                    }).encode()
                    self._send(200, "application/json", body)
                elif self.path == "/auth/state":
                    state = (dashboard.cloud.state() if dashboard.cloud
                             else {"configured": False, "devMode": True,
                                   "entitled": True, "loggedIn": False})
                    self._send(200, "application/json", json.dumps(state).encode())
                else:
                    self._send(404, "text/plain", b"not found")

            def do_POST(self):
                if not self._request_ok():
                    return
                if self.path == "/recap":
                    if dashboard.on_recap is not None:
                        try:
                            dashboard.on_recap()
                        except Exception:
                            log.exception("recap callback failed")
                    self._send(204, "text/plain", b"")
                elif self.path == "/auth/session" and dashboard.cloud:
                    length = int(self.headers.get("Content-Length", 0))
                    try:
                        tokens = json.loads(self.rfile.read(length))
                        state = dashboard.cloud.set_tokens(
                            tokens["access_token"], tokens["refresh_token"])
                        self._send(200, "application/json",
                                   json.dumps(state).encode())
                    except Exception:
                        log.exception("auth/session failed")
                        self._send(400, "text/plain", b"bad token payload")
                elif self.path == "/auth/refresh" and dashboard.cloud:
                    state = dashboard.cloud.refresh_entitlement()
                    self._send(200, "application/json", json.dumps(state).encode())
                elif self.path == "/auth/logout" and dashboard.cloud:
                    dashboard.cloud.logout()
                    self._send(204, "text/plain", b"")
                else:
                    self._send(404, "text/plain", b"not found")

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        log.info("dashboard at http://127.0.0.1:%d", port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
