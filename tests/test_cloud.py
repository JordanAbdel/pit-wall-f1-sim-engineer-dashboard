"""Cloud entitlement: the signed offline licence and the grace decision
(no network)."""
import base64
import json

import cloud
import ed25519
from cloud import LICENSE_GRACE_DAYS, grace_verdict, read_license_token

DAY = 86_400

# RFC 8032 §7.1 test 1 key pair. The token below was signed with its
# private half by the same WebCrypto code as supabase/functions/issue-license.
PUBLIC_KEY = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
IAT = 1_790_000_000
TOKEN = (
    "eyJ2IjoxLCJzdWIiOiJ1c2VyLTEiLCJlbWFpbCI6ImRyaXZlckBleGFtcGxlLmNvbSIsInN0"
    "YXR1cyI6ImFjdGl2ZSIsImlhdCI6MTc5MDAwMDAwMCwiZXhwIjoxNzkwNjA0ODAwfQ"
    ".2tacoDM1errSYLvRnpi3hJy7Qesul-43b39QqguGu9mCusrdADJgTyrO2JQ4d00UQVqs"
    "O6m2WRPoacJApwv6BQ")


def licence(status="active", iat=IAT, days=LICENSE_GRACE_DAYS):
    return {"status": status, "iat": iat, "exp": iat + days * DAY}


# ---- Ed25519 ----

def test_ed25519_rfc8032_test1():
    sig = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(PUBLIC_KEY, b"", sig)
    assert not ed25519.verify(PUBLIC_KEY, b"x", sig)
    assert not ed25519.verify(PUBLIC_KEY, b"", sig[:-1] + bytes([sig[-1] ^ 1]))


def test_ed25519_rejects_malformed_input():
    assert not ed25519.verify(b"short", b"", bytes(64))
    assert not ed25519.verify(PUBLIC_KEY, b"", bytes(10))
    assert not ed25519.verify(PUBLIC_KEY, b"", b"\xff" * 64)   # s >= q


# ---- licence token ----

def test_signed_token_verifies():
    payload = read_license_token(TOKEN, PUBLIC_KEY)
    assert payload["status"] == "active"
    assert payload["email"] == "driver@example.com"
    assert payload["exp"] - payload["iat"] == LICENSE_GRACE_DAYS * DAY


def test_edited_licence_is_rejected():
    body, sig = TOKEN.split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=="))
    forged["exp"] += 365 * DAY
    forged_body = base64.urlsafe_b64encode(
        json.dumps(forged).encode()).decode().rstrip("=")
    assert read_license_token(f"{forged_body}.{sig}", PUBLIC_KEY) is None


def test_old_unsigned_licence_format_is_rejected():
    # What license.json used to hold: plain JSON anyone could write.
    assert read_license_token(None, PUBLIC_KEY) is None
    assert read_license_token("not-a-token", PUBLIC_KEY) is None
    assert read_license_token(TOKEN + "x", PUBLIC_KEY) is None


def test_wrong_or_missing_key_rejects():
    other = bytes.fromhex(
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    assert read_license_token(TOKEN, other) is None
    assert read_license_token(TOKEN, None) is None


# ---- grace decision ----

def test_no_licence_means_no_grace():
    assert grace_verdict(None, IAT) == (False, 0.0)
    assert grace_verdict({}, IAT) == (False, 0.0)


def test_non_entitled_status_means_no_grace():
    assert grace_verdict(licence("canceled"), IAT) == (False, 0.0)
    assert grace_verdict(licence("inactive"), IAT) == (False, 0.0)


def test_past_due_keeps_grace():
    # Stripe is still retrying the card; don't cut the driver off.
    assert grace_verdict(licence("past_due"), IAT + DAY)[0]


def test_fresh_licence_grants_grace():
    entitled, left = grace_verdict(licence(), IAT + 2 * DAY)
    assert entitled and left == LICENSE_GRACE_DAYS - 2


def test_expired_licence_denies():
    entitled, left = grace_verdict(licence(), IAT + (LICENSE_GRACE_DAYS + 1) * DAY)
    assert not entitled and left == 0.0


def test_edge_of_grace_window():
    entitled, left = grace_verdict(licence(), IAT + (LICENSE_GRACE_DAYS - 0.1) * DAY)
    assert entitled and 0 < left <= 0.1


def test_future_issue_time_is_refused():
    # Winding the clock back to before the licence was issued must not
    # stretch it; that was the old unlimited-grace hole.
    assert grace_verdict(licence(), IAT - DAY) == (False, 0.0)


def test_grace_capped_even_if_exp_is_long():
    entitled, left = grace_verdict(licence(days=365), IAT + DAY)
    assert entitled and left == LICENSE_GRACE_DAYS - 1


def test_unreachable_exception_exists():
    # Unreachable (network) must stay distinguishable from ServerError
    # (server said no); the grace path depends on it.
    assert issubclass(cloud.Unreachable, RuntimeError)
    assert not issubclass(cloud.Unreachable, cloud.ServerError)


def test_server_error_message():
    e = cloud.ServerError("/x", 400, {"error_description": "Invalid login credentials"})
    assert e.message == "Invalid login credentials"
    assert cloud.ServerError("/x", 500, "oops").message is None


# ---- billing links ----

class FakeCloud(cloud.Cloud):
    """Cloud with the network replaced by a canned function response."""

    def __init__(self, response):
        self.configured = True
        self._access, self._refresh = "access", "refresh"
        self._response = response

    def _call_function(self, name):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_billing_link_accepts_stripe_urls_only():
    ok = "https://checkout.stripe.com/c/pay/cs_test_123"
    assert FakeCloud({"url": ok}).checkout_url() == {"url": ok}
    assert "error" in FakeCloud({"url": "https://evil.example/pay"}).checkout_url()
    assert "error" in FakeCloud({"url": "http://checkout.stripe.com/x"}).checkout_url()


def test_checkout_refusal_offers_manage():
    refused = cloud.ServerError("/functions/v1/create-checkout", 409,
                                {"error": "You already have a subscription.",
                                 "manage": True})
    out = FakeCloud(refused).checkout_url()
    assert out == {"error": "You already have a subscription.", "manage": True}


def test_billing_link_offline():
    out = FakeCloud(cloud.Unreachable("down")).portal_url()
    assert "internet" in out["error"]
