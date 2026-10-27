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

def test_traces_endpoint_versions_laps(dash):
    _, _, body = request(dash.port, "GET", "/traces.json")
    empty = json.loads(body)
    assert empty["last"] is None and empty["best"] is None
    lap = {"dist": [0, 10], "speed": [100, 120], "throttle": [100, 100],
           "brake": [0, 0], "time": [0, 300], "lapMs": 90_000}
    dash.set_traces(lap, None, "previous best")
    _, _, body = request(dash.port, "GET", "/traces.json")
    got = json.loads(body)
    assert got["version"] == empty["version"] + 1
    assert got["last"] == lap and got["bestLabel"] == "previous best"
    _, _, body = request(dash.port, "GET", "/state.json")
    assert json.loads(body)["tracesVersion"] == got["version"]


def test_radio_log_is_capped_and_ordered(dash):
    import dashboard as dashboard_mod
    for i in range(dashboard_mod.RADIO_KEEP + 5):
        dash.radio(f"line {i}", priority=(i == 0))
    _, _, body = request(dash.port, "GET", "/state.json")
    radio = json.loads(body)["radio"]
    assert len(radio) == dashboard_mod.RADIO_KEEP
    assert radio[-1]["text"] == f"line {dashboard_mod.RADIO_KEEP + 4}"
    ids = [r["id"] for r in radio]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


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
    assert request(port, "GET", "/traces.json", headers=h)[0] == 200


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


# ---- talking to the engineer, and his settings ----

def test_ask_reaches_the_engineer_and_returns_his_answer(dash):
    asked = []
    dash.on_ask = lambda q: asked.append(q) or "Loud and clear."
    status, _, body = request(dash.port, "POST", "/ask", {"q": "radio check"})
    assert status == 200 and json.loads(body)["answer"] == "Loud and clear."
    assert asked == ["radio check"]
    assert request(dash.port, "POST", "/ask", {"q": "   "})[0] == 400
    assert request(dash.port, "POST", "/ask", {"q": 5})[0] == 400
    request(dash.port, "POST", "/ask", {"q": "x" * 1000})
    assert len(asked[-1]) == 200


def test_paired_phone_can_ask_but_not_change_settings(dash):
    dash.on_ask = lambda q: "Copy."
    dash.on_settings = lambda body: {"ok": True}
    dash.settings_view = lambda: {"settings": {}}
    dash.on_radio_test = lambda: None
    dash.set_lan(True)
    h = {"Cookie": paired_cookie(dash)}
    port = dash._lan_port
    assert request(port, "POST", "/ask", {"q": "gap?"}, headers=h)[0] == 200
    assert request(port, "GET", "/settings", headers=h)[0] == 404
    assert request(port, "POST", "/settings", {"driverName": "X"}, headers=h)[0] == 403
    assert request(port, "POST", "/radio-test", headers=h)[0] == 403


def test_settings_round_trip_on_this_computer(dash):
    saved = []
    dash.settings_view = lambda: {"settings": {"driverName": "Jordan"}}
    dash.on_settings = lambda body: saved.append(body) or {"ok": True}
    status, _, body = request(dash.port, "GET", "/settings")
    assert status == 200 and json.loads(body)["settings"]["driverName"] == "Jordan"
    assert request(dash.port, "POST", "/settings", {"voice": "bm_george"})[0] == 200
    assert saved == [{"voice": "bm_george"}]
    # Cross-site pages may not change who the engineer thinks you are.
    assert request(dash.port, "POST", "/settings", {"driverName": "X"},
                   headers={"Origin": "http://evil.example"})[0] == 403


def test_driver_questions_show_in_the_radio_log(dash):
    dash.radio("What's the gap?", who="driver")
    dash.radio("The gap to the car ahead is a second.")
    _, _, body = request(dash.port, "GET", "/state.json")
    radio = json.loads(body)["radio"]
    assert [r["who"] for r in radio[-2:]] == ["driver", "engineer"]


def test_engineer_answers_over_the_radio_and_ai_is_optional():
    class Capture(SilentAnnouncer):
        def __init__(self):
            self.lines = []

        def say(self, text, priority=False):
            self.lines.append(text)

    class FakeAI:
        enabled = True

        def __init__(self):
            self.asked = []

        def ask(self, q, ctx):
            self.asked.append((q, ctx))
            return "Box this lap, we'll go to mediums."

    d = Dashboard()
    ann, fake_ai = Capture(), FakeAI()
    eng = main_mod.Engineer(ann, dash=d, cloud=None, driver_name="Jordan",
                            ai=fake_ai)
    # Scripted questions never go to the AI.
    eng.ask("radio check")
    assert fake_ai.asked == []
    assert "loud and clear" in ann.lines[-1].lower() \
        or "five by five" in ann.lines[-1].lower()
    # Unscripted ones do, with the live session as context.
    eng.ask("should I pit this lap?")
    assert fake_ai.asked and "position" in fake_ai.asked[0][1]
    assert ann.lines[-1].endswith("we'll go to mediums.")
    radio = d.snapshot()["radio"]
    assert radio[-2]["who"] == "driver" and radio[-2]["text"] == "should I pit this lap?"
    # With the AI unavailable the engineer asks again rather than going silent.
    fake_ai.ask = lambda q, ctx: None
    eng.ask("should I pit this lap?")
    assert "say again" in ann.lines[-1].lower()


def test_settings_apply_to_the_engineer(tmp_path, monkeypatch):
    import settings as settings_mod
    import speak
    monkeypatch.setattr(settings_mod, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(speak, "output_devices", lambda: [(3, "BlackHole 2ch")])

    class Configurable(SilentAnnouncer):
        def __init__(self):
            self.cfg = None

        def configure(self, **kw):
            self.cfg = kw

    ann = Configurable()
    eng = main_mod.Engineer(ann, dash=Dashboard(), cloud=None)
    try:
        view = eng.apply_settings({"driverName": "Jordan<script>", "voice": "bm_george",
                                   "radioFx": False, "audioDevice": "blackhole"})
        assert view["settings"]["driverName"] == "Jordanscript"
        assert eng.persona.name == "Jordanscript"
        assert ann.cfg == {"voice": "bm_george", "radio_fx": False}
        assert view["activeDevice"] == "BlackHole 2ch"
        assert json.loads((tmp_path / "settings.json").read_text())["voice"] == "bm_george"
        eng.apply_settings({"voice": "not-a-voice"})
        assert eng.settings["voice"] == "bm_george"
    finally:
        speak.set_output_device(None)
