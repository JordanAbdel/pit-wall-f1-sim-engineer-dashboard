"""Dashboard HTTP: the localhost app, the paired-device LAN view, and the
connection check that works before the paywall."""
import http.client
import json
import socket

import pytest

import lan
import main as main_mod
from dashboard import Dashboard


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StubCloud:
    configured = True

    def __init__(self, entitled=True):
        self.entitled = entitled
        self.logins = []

    def state(self):
        return {"configured": True, "devMode": False, "loggedIn": True,
                "email": "driver@example.com", "status": "active",
                "entitled": self.entitled}

    def login(self, email, password, signup=False):
        self.logins.append((email, password, signup))
        return {"ok": True, "message": None}

    def portal_url(self):
        return {"url": "https://billing.stripe.com/p/session/test"}

    def checkout_url(self):
        return {"error": "You already have a subscription.", "manage": True}


@pytest.fixture
def dash(tmp_path):
    d = Dashboard()
    d.lan = lan.LanAccess(tmp_path / "lan.json")
    d._lan_port = free_port()
    d.cloud = StubCloud()
    d.port = free_port()
    d.start(d.port)
    yield d
    d.stop()


def request(port, method, path, body=None, host=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=data, headers=hdrs)
    res = conn.getresponse()
    out = res.status, dict(res.getheaders()), res.read()
    conn.close()
    return out


# ---- localhost app ----

def test_page_loads_no_third_party_script(dash):
    status, headers, body = request(dash.port, "GET", "/")
    assert status == 200
    assert b"esm.sh" not in body and b"supabase-js" not in body
    assert "script-src 'self' 'unsafe-inline'" in headers["Content-Security-Policy"]
    assert "connect-src 'self'" in headers["Content-Security-Policy"]


def test_config_exposes_no_supabase_keys(dash):
    _, _, body = request(dash.port, "GET", "/config.json")
    cfg = json.loads(body)
    assert "supabaseAnonKey" not in cfg and "checkoutUrl" not in cfg
    assert cfg["remote"] is False


def test_login_goes_through_the_app(dash):
    status, _, body = request(dash.port, "POST", "/auth/login",
                              {"email": " d@x.com ", "password": "pw", "signup": False})
    assert status == 200 and json.loads(body)["ok"]
    assert dash.cloud.logins == [("d@x.com", "pw", False)]
    # The old browser-token handoff is gone.
    assert request(dash.port, "POST", "/auth/session", {})[0] == 404


def test_login_rejects_bad_payload_and_huge_body(dash):
    assert request(dash.port, "POST", "/auth/login", {"email": 1})[0] == 400
    status, _, _ = request(dash.port, "POST", "/auth/login",
                           {"email": "a", "password": "x" * 20_000})
    assert status == 400


def test_billing_links(dash):
    _, _, body = request(dash.port, "POST", "/billing/portal")
    assert json.loads(body)["url"].startswith("https://billing.stripe.com/")
    _, _, body = request(dash.port, "POST", "/billing/checkout")
    assert json.loads(body)["manage"] is True


def test_cross_site_post_refused(dash):
    status, _, _ = request(dash.port, "POST", "/billing/portal",
                           headers={"Origin": "https://evil.example"})
    assert status == 403
    assert request(dash.port, "GET", "/state.json", host="evil.example")[0] == 403


# ---- LAN (phone / tablet) ----

def test_lan_off_by_default(dash):
    assert not dash.lan.enabled
    with pytest.raises(OSError):
        request(dash._lan_port, "GET", "/")


def paired_cookie(dash):
    status, headers, _ = request(dash._lan_port, "POST", "/pair",
                                 {"code": dash.lan.code})
    assert status == 204
    return headers["Set-Cookie"].split(";")[0]


def test_lan_requires_pairing(dash):
    state = dash.set_lan(True)
    assert state["running"] and len(state["code"]) == 6
    port = dash._lan_port
    host = f"192.168.1.20:{port}"
    # Unpaired: the pairing page, and nothing else.
    status, _, body = request(port, "GET", "/", host=host)
    assert status == 200 and b"PAIR THIS DEVICE" in body
    assert request(port, "GET", "/state.json", host=host)[0] == 403
    wrong = "111111" if dash.lan.code != "111111" else "222222"
    assert request(port, "POST", "/pair", {"code": wrong}, host=host)[0] == 403

    cookie = paired_cookie(dash)
    assert "HttpOnly" in request(port, "POST", "/pair",
                                 {"code": dash.lan.code})[1]["Set-Cookie"]
    status, _, body = request(port, "GET", "/state.json", host=host,
                              headers={"Cookie": cookie})
    assert status == 200 and "laps" in json.loads(body)
    status, _, body = request(port, "GET", "/", host=host, headers={"Cookie": cookie})
    assert status == 200 and b"PIT WALL" in body


def test_lan_is_view_only(dash):
    dash.set_lan(True)
    cookie = paired_cookie(dash)
    port, h = dash._lan_port, {"Cookie": cookie}
    for path in ("/auth/login", "/auth/logout", "/billing/portal",
                 "/billing/checkout", "/lan", "/lan/unpair"):
        assert request(port, "POST", path, {}, headers=h)[0] == 403, path
    assert request(port, "GET", "/lan", headers=h)[0] == 404
    _, _, body = request(port, "GET", "/auth/state", headers=h)
    state = json.loads(body)
    assert state["remote"] is True and "email" not in state
    _, _, body = request(port, "GET", "/config.json", headers=h)
    assert json.loads(body)["remote"] is True
    assert request(port, "POST", "/recap", headers=h)[0] == 204


def test_pair_via_url_code_redirects(dash):
    dash.set_lan(True)
    status, headers, _ = request(dash._lan_port, "GET", f"/?code={dash.lan.code}")
    assert status == 303 and headers["Location"] == "/"
    assert headers["Set-Cookie"].startswith(lan.COOKIE + "=")


def test_wrong_codes_rotate_the_code(dash):
    dash.set_lan(True)
    original = dash.lan.code
    wrong = "111111" if original != "111111" else "222222"
    for _ in range(lan.MAX_FAILED_CODES):
        request(dash._lan_port, "POST", "/pair", {"code": wrong})
    assert dash.lan.code != original
    assert not dash.lan.try_pair(original)


def test_unpair_all_revokes_devices(dash):
    dash.set_lan(True)
    cookie = paired_cookie(dash)
    request(dash.port, "POST", "/lan/unpair")
    assert request(dash._lan_port, "GET", "/state.json",
                   headers={"Cookie": cookie})[0] == 403


def test_lan_turns_off(dash):
    dash.set_lan(True)
    assert dash.set_lan(False)["running"] is False
    with pytest.raises(OSError):
        request(dash._lan_port, "GET", "/")


def test_lan_setting_persists(tmp_path):
    a = lan.LanAccess(tmp_path / "lan.json")
    a.set_enabled(True)
    b = lan.LanAccess(tmp_path / "lan.json")
    assert b.enabled
    assert b.device_ok(a.set_cookie_header().split(";")[0])


# ---- connection check before paying ----

class SilentAnnouncer:
    def say(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_unpaid_user_sees_console_connect():
    dash = Dashboard()
    eng = main_mod.Engineer(SilentAnnouncer(), dash=dash,
                            cloud=StubCloud(entitled=False))
    data = bytearray(100)   # only the format field is read before the paywall
    data[0:2] = (2024).to_bytes(2, "little")
    eng.handle(bytes(data))
    snap = dash.snapshot()
    assert snap["hasSignal"] and snap["signalGame"] == "F1 24"
    assert snap["laps"] == [] and snap["lapNo"] is None   # nothing processed

    data[0:2] = (2019).to_bytes(2, "little")
    eng.handle(bytes(data))
    snap = dash.snapshot()
    assert snap["signalFormat"] == 2019 and snap["signalGame"] is None
