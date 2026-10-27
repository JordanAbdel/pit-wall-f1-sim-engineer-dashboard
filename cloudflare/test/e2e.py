#!/usr/bin/env python3
"""End-to-end test of the licence API, run locally — no Cloudflare account,
no Stripe account, nothing leaves this machine.

    cd race-engineer && build-venv/bin/python cloudflare/test/e2e.py

Starts a fake Stripe API, runs the Worker under `wrangler dev` (local D1,
throwaway secrets), then drives it two ways: raw HTTP for the edge cases,
and the app's own cloud.Cloud for the real sign-in -> subscribe -> licence
flow the dashboard uses.
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
WORKER = HERE.parent
APP = WORKER.parent
sys.path.insert(0, str(APP))

WEBHOOK_SECRET = "whsec_test_e2e"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- fake Stripe ------------------------------------------------------------

class FakeStripe:
    def __init__(self):
        self.customers = {}       # id -> {email, metadata}
        self.subs = {}            # customer -> [sub]
        self.checkouts = []
        self.calls = []

    def handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                fake.calls.append(("GET", u.path, q))
                if u.path == "/v1/subscriptions":
                    return self._send(200, {"data": fake.subs.get(q["customer"], [])})
                if u.path == "/v1/checkout/sessions":
                    return self._send(200, {"data": []})
                if u.path.startswith("/v1/customers/"):
                    cid = u.path.rsplit("/", 1)[1]
                    c = fake.customers.get(cid)
                    return self._send(200 if c else 404, {"id": cid, **(c or {})})
                self._send(404, {"error": {"message": "no route"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                fake.calls.append(("POST", self.path, form))
                assert self.headers["Authorization"] == "Bearer sk_test_fake"
                if self.path == "/v1/customers":
                    cid = f"cus_{len(fake.customers) + 1}"
                    fake.customers[cid] = {"email": form["email"],
                                           "metadata": {"user_id": form["metadata[user_id]"]}}
                    return self._send(200, {"id": cid})
                if self.path == "/v1/checkout/sessions":
                    fake.checkouts.append(form)
                    return self._send(200, {"url": "https://checkout.stripe.com/c/pay/cs_test_1"})
                if self.path == "/v1/billing_portal/sessions":
                    return self._send(200, {"url": "https://billing.stripe.com/p/session/test_1"})
                self._send(404, {"error": {"message": "no route"}})
        return H


# ---- helpers -----------------------------------------------------------------

def call(base, method, path, body=None, token=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def stripe_event(base, etype, obj, secret=WEBHOOK_SECRET, ts=None):
    payload = json.dumps({"id": f"evt_{etype}", "type": etype,
                          "data": {"object": obj}})
    ts = int(ts if ts is not None else time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return call(base, "POST", "/functions/v1/stripe-webhook", raw=payload.encode(),
                headers={"stripe-signature": f"t={ts},v1={sig}"})


def check(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        raise SystemExit(1)


# ---- run ---------------------------------------------------------------------

def main():
    keys = subprocess.run(["node", str(APP / "scripts" / "gen_license_keys.mjs")],
                          capture_output=True, text=True, check=True).stdout
    kv = dict(line.split("=", 1) for line in keys.split())
    stripe = FakeStripe()
    sport = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", sport), stripe.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    state = Path(tempfile.mkdtemp(prefix="pitwall-e2e-"))
    vars_path = WORKER / ".dev.vars"
    had_vars = vars_path.exists()
    if had_vars:
        shutil.copy(vars_path, state / "dev.vars.bak")
    vars_path.write_text(
        "AUTH_SECRET=e2e-auth-secret\nSTRIPE_SECRET_KEY=sk_test_fake\n"
        f"STRIPE_WEBHOOK_SECRET={WEBHOOK_SECRET}\nSTRIPE_PRICE_ID=price_test_1\n"
        f"LICENSE_SIGNING_KEY={kv['LICENSE_SIGNING_KEY']}\n"
        f"STRIPE_API_BASE=http://127.0.0.1:{sport}\n")
    port = free_port()
    persist = str(state / "wrangler")
    wr = [str(WORKER / "node_modules" / ".bin" / "wrangler")]
    subprocess.run(wr + ["d1", "execute", "pitwall", "--local", "--persist-to", persist,
                         "--file", "schema.sql"], cwd=WORKER, check=True,
                   capture_output=True)
    dev = subprocess.Popen(wr + ["dev", "--port", str(port), "--persist-to", persist,
                                 "--ip", "127.0.0.1"],
                           cwd=WORKER, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                if call(base, "GET", "/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit("wrangler dev did not come up")
        run_checks(base, stripe, kv)
    finally:
        dev.terminate()
        try:
            dev.wait(10)
        except subprocess.TimeoutExpired:
            dev.kill()
        srv.shutdown()
        if had_vars:
            shutil.copy(state / "dev.vars.bak", vars_path)
        else:
            vars_path.unlink(missing_ok=True)
        shutil.rmtree(state, ignore_errors=True)
    print("all good")


def run_checks(base, stripe, kv):
    print("auth")
    s, r = call(base, "POST", "/auth/v1/signup", {"email": "Driver@Example.com", "password": "short"})
    check(s == 400 and "8 characters" in r["error_description"], "short password refused")
    s, r = call(base, "POST", "/auth/v1/signup", {"email": "Driver@Example.com", "password": "hunter22pw"})
    check(s == 200 and r["access_token"] and r["user"]["email"] == "driver@example.com",
          "sign-up returns a session, email lower-cased")
    uid = r["user"]["id"]
    s, r = call(base, "POST", "/auth/v1/signup", {"email": "driver@example.com", "password": "hunter22pw"})
    check(s == 400 and r["error_description"] == "User already registered", "duplicate sign-up refused")
    s, r = call(base, "POST", "/auth/v1/token?grant_type=password", {"email": "driver@example.com", "password": "wrong-one"})
    check(s == 400 and r["error_description"] == "Invalid login credentials", "wrong password refused")
    s, r = call(base, "POST", "/auth/v1/token?grant_type=password", {"email": "nobody@example.com", "password": "whatever1"})
    check(s == 400 and r["error_description"] == "Invalid login credentials", "unknown email gets the same answer")
    s, sess = call(base, "POST", "/auth/v1/token?grant_type=password", {"email": "DRIVER@example.com", "password": "hunter22pw"})
    check(s == 200, "right password signs in")
    s, r = call(base, "GET", "/auth/v1/user", token=sess["access_token"])
    check(s == 200 and r["id"] == uid, "access token identifies the user")
    s, _ = call(base, "GET", "/auth/v1/user", token=sess["access_token"][:-3] + "abc")
    check(s == 401, "tampered access token refused")
    s, new = call(base, "POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": sess["refresh_token"]})
    check(s == 200 and new["refresh_token"] != sess["refresh_token"], "refresh rotates the token")
    s, _ = call(base, "POST", "/auth/v1/token?grant_type=refresh_token", {"refresh_token": sess["refresh_token"]})
    check(s == 401, "a used refresh token is dead")
    tok = new["access_token"]

    for _ in range(10):
        call(base, "POST", "/auth/v1/token?grant_type=password", {"email": "locked@example.com", "password": "nope-nope"})
    s, r = call(base, "POST", "/auth/v1/token?grant_type=password", {"email": "locked@example.com", "password": "nope-nope"})
    check(s == 429, "10 failed sign-ins lock the email for a while")

    print("profile + licence")
    s, r = call(base, "GET", f"/rest/v1/profiles?id=eq.{uid}&select=subscription_status,email", token=tok)
    check(s == 200 and r == [{"subscription_status": "inactive", "email": "driver@example.com"}], "new profile is inactive")
    s, r = call(base, "GET", "/rest/v1/profiles?id=eq.someone-else", token=tok)
    check(s == 200 and r == [], "can't read someone else's profile")
    s, _ = call(base, "POST", "/functions/v1/issue-license", {}, token=tok)
    check(s == 403, "no licence without a subscription")
    s, _ = call(base, "POST", "/functions/v1/create-checkout", {})
    check(s == 401, "billing needs a signed-in user")

    print("billing")
    s, r = call(base, "POST", "/functions/v1/create-portal-session", {}, token=tok)
    check(s == 404, "portal refused before there's a customer")
    s, r = call(base, "POST", "/functions/v1/create-checkout", {}, token=tok)
    check(s == 200 and r["url"].startswith("https://checkout.stripe.com/"), "checkout URL returned")
    co = stripe.checkouts[-1]
    check(co["line_items[0][price]"] == "price_test_1" and co["client_reference_id"] == uid
          and co["subscription_data[metadata][user_id]"] == uid, "checkout carries price and user")
    cust = next(iter(stripe.customers))

    s, _ = stripe_event(base, "customer.subscription.created", {"customer": cust}, secret="whsec_wrong")
    check(s == 400, "webhook with a bad signature refused")
    s, _ = stripe_event(base, "customer.subscription.created", {"customer": cust}, ts=time.time() - 3600)
    check(s == 400, "replayed (old) webhook refused")
    stripe.subs[cust] = [{"id": "sub_1", "status": "active", "created": 100,
                          "current_period_end": int(time.time()) + 30 * 86400}]
    s, _ = stripe_event(base, "checkout.session.completed", {"customer": cust, "client_reference_id": uid})
    check(s == 200, "signed webhook accepted")
    s, r = call(base, "GET", f"/rest/v1/profiles?id=eq.{uid}", token=tok)
    check(r[0]["subscription_status"] == "active", "subscription is now active")
    s, r = call(base, "POST", "/functions/v1/create-checkout", {}, token=tok)
    check(s == 409 and r.get("manage") is True, "second checkout refused, offers manage")
    s, r = call(base, "POST", "/functions/v1/create-portal-session", {}, token=tok)
    check(s == 200 and r["url"].startswith("https://billing.stripe.com/"), "portal URL returned")

    import cloud

    s, r = call(base, "POST", "/functions/v1/issue-license", {}, token=tok)
    pub = base64.b64decode(kv["LICENSE_PUBLIC_KEY"])
    lic = cloud.read_license_token(r["token"], pub)
    check(s == 200 and lic and lic["status"] == "active" and lic["sub"] == uid,
          "licence is signed and verifies with the app's public key")

    stripe.subs[cust] = [{"id": "sub_1", "status": "canceled", "created": 100,
                          "current_period_end": int(time.time())}]
    stripe_event(base, "customer.subscription.deleted", {"customer": cust})
    s, r = call(base, "GET", f"/rest/v1/profiles?id=eq.{uid}", token=tok)
    check(r[0]["subscription_status"] == "canceled", "cancellation revokes access")

    print("the app's own client (cloud.Cloud)")
    import config
    tmp = Path(tempfile.mkdtemp(prefix="pitwall-app-"))
    config.SUPABASE_URL = base
    config.SUPABASE_ANON_KEY = ""
    config.API_URL = base
    config.LICENSE_PUBLIC_KEY = kv["LICENSE_PUBLIC_KEY"]
    cloud.AUTH_PATH, cloud.LICENSE_PATH = tmp / "auth.json", tmp / "license.json"
    c = cloud.Cloud()
    time.sleep(1)
    check(c.configured and not c.entitled, "app starts locked with no login")
    out = c.login("racer@example.com", "pitwall-pass-1", signup=True)
    check(out["ok"] and c.logged_in and c.status == "inactive", "app sign-up works")
    link = c.checkout_url()
    check(link.get("url", "").startswith("https://checkout.stripe.com/"), "app gets a checkout link")
    cust2 = [k for k, v in stripe.customers.items() if v["email"] == "racer@example.com"][0]
    stripe.subs[cust2] = [{"id": "sub_2", "status": "active", "created": 200,
                           "current_period_end": int(time.time()) + 30 * 86400}]
    stripe_event(base, "invoice.paid", {"customer": cust2})
    st = c.refresh_entitlement()
    check(st["entitled"] and st["status"] == "active", "app sees the subscription after payment")
    check(cloud.LICENSE_PATH.exists(), "app cached a signed offline licence")
    c2 = cloud.Cloud()   # a fresh launch, using the saved login
    for _ in range(40):
        if c2.status:
            break
        time.sleep(0.25)
    check(c2.entitled and c2.status == "active" and not c2.state()["offline"],
          "relaunch signs in from the saved token and stays entitled")
    check(c.portal_url().get("url", "").startswith("https://billing.stripe.com/"), "app gets a portal link")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
