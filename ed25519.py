"""Ed25519 signature verification (RFC 8032), pure Python, verify-only.

Used to check the server-signed offline licence (see cloud.py). Verify-only
on purpose: the app never holds a signing key. This is the RFC 8032 §6
reference algorithm — slow by crypto standards (~10 ms per verify) but the
app verifies one licence at startup, and it keeps the frozen build free of
a native crypto dependency.
"""
import hashlib

_P = 2 ** 255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _P
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _P
    C = 2 * P[3] * Q[3] * _D % _P
    D = 2 * P[2] * Q[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    return ((P[0] * Q[2] - Q[0] * P[2]) % _P == 0
            and (P[1] * Q[2] - Q[1] * P[2]) % _P == 0)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _decompress(s: bytes):
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True iff `signature` is a valid Ed25519 signature of `message`
    under the 32-byte raw `public_key`. Never raises on bad input."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    A = _decompress(public_key)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(
        signature[:32] + public_key + message).digest(), "little") % _Q
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))
