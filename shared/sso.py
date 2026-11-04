"""shared/sso.py — SSO token: b64url(json).b64url(hmac_sha256), หมดอายุ 60 วินาที, nonce ใช้ได้ครั้งเดียว"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid


class SsoError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code          # malformed | bad_signature | expired


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise SsoError("malformed") from exc


def _mac(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def sign_token(secret: str, claims: dict, ttl: int = 60, now: float | None = None) -> str:
    now = time.time() if now is None else now
    body = {**claims, "iat": now, "exp": now + ttl, "nonce": uuid.uuid4().hex}
    payload = _b64(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return payload + "." + _mac(secret, payload)


def verify_token(secret: str, token: str, now: float | None = None) -> dict:
    parts = (token or "").split(".")
    if len(parts) != 2 or not all(parts):
        raise SsoError("malformed")
    payload, sig = parts
    if not hmac.compare_digest(sig, _mac(secret, payload)):
        # a garbage payload with a garbage signature is malformed; a decodable one is a bad signature
        try:
            json.loads(_unb64(payload))
        except (SsoError, ValueError, UnicodeDecodeError):
            raise SsoError("malformed") from None
        raise SsoError("bad_signature")
    try:
        claims = json.loads(_unb64(payload))
        exp = float(claims["exp"])
    except (SsoError, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise SsoError("malformed") from exc
    if (time.time() if now is None else now) > exp:
        raise SsoError("expired")
    return claims
