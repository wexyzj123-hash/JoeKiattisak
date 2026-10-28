import base64
import json

import pytest

from shared.sso import SsoError, sign_token, verify_token

SECRET = "s" * 40
CLAIMS = {"uid": 1, "name": "สมชาย ใจดี", "roles": ["admin"]}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_roundtrip_returns_claims():
    out = verify_token(SECRET, sign_token(SECRET, CLAIMS))
    assert out["uid"] == 1 and out["name"] == "สมชาย ใจดี" and out["roles"] == ["admin"]
    assert len(out["nonce"]) == 32


def test_tampered_payload_rejected():
    tok = sign_token(SECRET, CLAIMS)
    forged = _b64(json.dumps({**CLAIMS, "uid": 99, "exp": 9e12, "nonce": "x"}).encode())
    with pytest.raises(SsoError) as e:
        verify_token(SECRET, forged + "." + tok.split(".")[1])
    assert e.value.code == "bad_signature"


def test_wrong_secret_rejected():
    with pytest.raises(SsoError) as e:
        verify_token("z" * 40, sign_token(SECRET, CLAIMS))
    assert e.value.code == "bad_signature"


def test_expired_rejected():
    tok = sign_token(SECRET, CLAIMS, now=1000.0)
    with pytest.raises(SsoError) as e:
        verify_token(SECRET, tok, now=1061.0)
    assert e.value.code == "expired"


def test_valid_at_59s():
    tok = sign_token(SECRET, CLAIMS, now=1000.0)
    assert verify_token(SECRET, tok, now=1059.0)["uid"] == 1


@pytest.mark.parametrize("bad", ["", "abc", "a.b.c", "!!!.???"])
def test_malformed_rejected(bad):
    with pytest.raises(SsoError) as e:
        verify_token(SECRET, bad)
    assert e.value.code == "malformed"


def test_nonces_unique():
    assert sign_token(SECRET, CLAIMS) != sign_token(SECRET, CLAIMS)
    n1 = verify_token(SECRET, sign_token(SECRET, CLAIMS))["nonce"]
    n2 = verify_token(SECRET, sign_token(SECRET, CLAIMS))["nonce"]
    assert n1 != n2
