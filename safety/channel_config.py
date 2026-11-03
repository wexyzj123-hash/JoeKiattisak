"""safety/channel_config.py — ตั้งค่าช่องทางแจ้งเตือน (อีเมล SMTP / Teams) จากหน้าเว็บ โดยผู้ดูแลระบบ

- ค่าที่ตั้งบนหน้าเว็บมีผลก่อนค่าในไฟล์ .env (เว้นว่าง = ใช้ค่าจาก .env เหมือนเดิม) — เครื่องที่ตั้งผ่าน .env อยู่แล้วทำงานต่อได้ไม่ต้องแก้
- ความลับ (รหัสผ่าน SMTP, URL ของ Teams กลาง) เป็น "เขียนอย่างเดียว": เก็บแบบเข้ารหัสในฐานข้อมูล ไม่แสดงกลับบนหน้าเว็บ ไม่เขียนลง log
  (เข้ารหัสด้วย HMAC-SHA256 แบบ CTR + แท็กตรวจความถูกต้อง คีย์มาจาก SECRET_KEY ของแอป — ไม่ต้องติดตั้งไลบรารีเพิ่ม)
- ตัวส่งทุกตัวรับ Mapping แบบเดียวกับ environment (SMTP_HOST ฯลฯ) จึงใช้ effective_env() แทน os.environ ได้ทันที"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

PREFIX = "chan."
ENC = "enc1:"
# ชื่อฟิลด์ในฟอร์ม → (ชื่อตัวแปร environment ที่ตัวส่งอ่าน, เป็นความลับหรือไม่)
FIELDS = {
    "smtp_host": ("SMTP_HOST", False), "smtp_port": ("SMTP_PORT", False), "smtp_from": ("SMTP_FROM", False),
    "smtp_user": ("SMTP_USER", False), "smtp_tls": ("SMTP_TLS", False), "smtp_password": ("SMTP_PASSWORD", True),
    "teams_webhook": ("TEAMS_WEBHOOK_URL", True), "teams_allowed_hosts": ("TEAMS_ALLOWED_HOSTS", False),
    "http_timeout": ("NOTIFY_HTTP_TIMEOUT", False),
}
TLS_MODES = {"": "ไม่เข้ารหัส (พอร์ต 25 ภายในองค์กร)", "starttls": "STARTTLS (พอร์ต 587)", "ssl": "SSL/TLS (พอร์ต 465)"}
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,200}$")
_state = {"db_path": None, "key": b"", "cache": {}, "lock": threading.Lock()}
CACHE_SECONDS = 5.0


def configure(db_path: str, secret_key: str) -> None:
    """เรียกครั้งเดียวตอนสร้างแอป: ผูกฐานข้อมูลและคีย์เข้ารหัส (ใช้โดยตัวส่งที่ไม่มี request)"""
    _state["db_path"] = db_path
    _state["key"] = hashlib.sha256(("channel-config:" + str(secret_key)).encode()).digest()
    _state["cache"] = {}


# ---------- เข้ารหัส/ถอดรหัส ----------
def _keys() -> tuple[bytes, bytes]:
    k = _state["key"] or hashlib.sha256(b"channel-config:unset").digest()
    return hmac.new(k, b"enc", "sha256").digest(), hmac.new(k, b"mac", "sha256").digest()


def encrypt(plain: str) -> str:
    ek, mk = _keys()
    nonce = secrets.token_bytes(16)
    data = plain.encode("utf-8")
    stream = b"".join(hmac.new(ek, nonce + i.to_bytes(4, "big"), "sha256").digest() for i in range((len(data) + 31) // 32))
    ct = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(mk, nonce + ct, "sha256").digest()
    return ENC + base64.urlsafe_b64encode(nonce + ct + tag).decode()


def decrypt(blob: str) -> str:
    """คืนข้อความเดิม; ถ้าถูกแก้/คีย์เปลี่ยน คืนสตริงว่าง (ถือว่ายังไม่ได้ตั้งค่า) — ไม่ throw"""
    if not blob.startswith(ENC):
        return ""
    try:
        raw = base64.urlsafe_b64decode(blob[len(ENC):].encode())
        nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
        ek, mk = _keys()
        if not hmac.compare_digest(tag, hmac.new(mk, nonce + ct, "sha256").digest()):
            return ""
        stream = b"".join(hmac.new(ek, nonce + i.to_bytes(4, "big"), "sha256").digest() for i in range((len(ct) + 31) // 32))
        return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


# ---------- อ่านค่า ----------
def stored(db) -> dict[str, str]:
    """ค่าที่ผู้ดูแลตั้งบนหน้าเว็บ (ถอดรหัสแล้ว) ตามชื่อฟิลด์ — เฉพาะที่ไม่ว่าง"""
    rows = db.execute("SELECT key, value FROM app_settings WHERE key LIKE ?", (PREFIX + "%",)).fetchall()
    out = {}
    for r in rows:
        name = r["key"][len(PREFIX):]
        if name in FIELDS and r["value"]:
            out[name] = decrypt(r["value"]) if FIELDS[name][1] else r["value"]
    return {k: v for k, v in out.items() if v}


def _overrides(db=None) -> dict[str, str]:
    path = _state["db_path"]
    now = time.monotonic()
    if db is None:
        with _state["lock"]:
            hit = _state["cache"].get(path)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
    if db is None:
        if not path:
            return {}
        from .db import connect
        con = connect(path)
        try:
            values = stored(con)
        except Exception:                                    # noqa: BLE001 — ตารางยังไม่พร้อม = ไม่มีค่าตั้งบนเว็บ
            values = {}
        finally:
            con.close()
    else:
        values = stored(db)
    mapped = {FIELDS[k][0]: v for k, v in values.items()}
    if db is None:
        with _state["lock"]:
            _state["cache"][path] = (now, mapped)
    return mapped


def effective_env(db=None) -> dict[str, str]:
    """environment ที่ตัวส่งใช้: ค่าจาก .env/ระบบ แล้วทับด้วยค่าที่ตั้งบนหน้าเว็บ (ถ้ามี)"""
    return {**os.environ, **_overrides(db)}


def source_of(name: str, web: dict[str, str]) -> str:
    if name in web:
        return "web"
    return "env" if os.environ.get(FIELDS[name][0]) else "none"


# ---------- บันทึกจากฟอร์ม ----------
def _host_ok(value: str) -> bool:
    return bool(HOST_RE.match(value)) and ".." not in value


def validate(form, current: dict[str, str]) -> tuple[dict[str, str | None], list[str]]:
    """คืน ({ฟิลด์: ค่าใหม่ | None=ลบ}, ข้อผิดพลาด) — ฟิลด์ที่ไม่อยู่ใน dict = คงค่าเดิม"""
    from .notify import teams_host_ok
    errors: list[str] = []
    out: dict[str, str | None] = {}
    g = lambda k: (form.get(k) or "").strip()

    host = g("smtp_host")
    if host and not _host_ok(host):
        errors.append("เซิร์ฟเวอร์ SMTP ไม่ถูกต้อง (ใส่ชื่อโฮสต์หรือ IP เท่านั้น ไม่ต้องใส่ http://)")
    out["smtp_host"] = host or None
    port = g("smtp_port")
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        errors.append("พอร์ต SMTP ต้องเป็นจำนวนเต็ม 1-65535")
    out["smtp_port"] = port or None
    sender = g("smtp_from")
    if sender and not EMAIL_RE.match(sender):
        errors.append("อีเมลผู้ส่งไม่ถูกต้อง")
    out["smtp_from"] = sender or None
    user = g("smtp_user")
    if len(user) > 200 or re.search(r"[\x00-\x1f]", user):
        errors.append("ชื่อผู้ใช้ SMTP ไม่ถูกต้อง")
    out["smtp_user"] = user or None
    tls = g("smtp_tls")
    if tls not in TLS_MODES:
        errors.append("รูปแบบการเข้ารหัส SMTP ไม่ถูกต้อง")
    out["smtp_tls"] = tls or None
    timeout = g("http_timeout")
    if timeout and not (timeout.isdigit() and 1 <= int(timeout) <= 60):
        errors.append("เวลารอสูงสุดต้องเป็น 1-60 วินาที")
    out["http_timeout"] = timeout or None
    extra = g("teams_allowed_hosts")
    if extra and not all(_host_ok(h.strip()) for h in extra.split(",") if h.strip()):
        errors.append("โดเมน Teams ที่อนุญาตเพิ่มต้องเป็นชื่อโฮสต์ คั่นด้วยเครื่องหมายจุลภาค")
    out["teams_allowed_hosts"] = extra or None

    # ความลับ: ว่าง = คงเดิม, ติ๊ก "ลบ" = ล้าง, มีค่า = แทนที่
    pw = form.get("smtp_password") or ""
    if form.get("clear_smtp_password"):
        out["smtp_password"] = None
    elif pw:
        if len(pw) > 300 or re.search(r"[\x00-\x1f]", pw):
            errors.append("รหัสผ่าน SMTP ไม่ถูกต้อง")
        out["smtp_password"] = pw
    hook = g("teams_webhook")
    if form.get("clear_teams_webhook"):
        out["teams_webhook"] = None
    elif hook:
        probe = {"TEAMS_ALLOWED_HOSTS": extra or current.get("teams_allowed_hosts", "") or os.environ.get("TEAMS_ALLOWED_HOSTS", "")}
        if len(hook) > 600 or re.search(r"[\s\x00-\x1f\x7f]", hook) or not teams_host_ok(hook, probe):
            errors.append("URL ของ Teams ต้องเป็น https และอยู่ในโดเมนของ Microsoft (หรือโดเมนที่อนุญาตเพิ่มด้านล่าง)")
        out["teams_webhook"] = hook
    return out, errors


def save(db, form) -> list[str]:
    changes, errors = validate(form, stored(db))
    if errors:
        return errors
    for name, value in changes.items():
        key = PREFIX + name
        if value is None:
            db.execute("DELETE FROM app_settings WHERE key=?", (key,))
        else:
            db.execute("INSERT INTO app_settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, encrypt(value) if FIELDS[name][1] else value))
    db.commit()
    with _state["lock"]:
        _state["cache"] = {}
    return []


def form_view(db) -> dict:
    """ค่าสำหรับแสดงในฟอร์ม: ฟิลด์ปกติแสดงค่าที่ตั้งบนเว็บ; ความลับแสดงเพียงว่าตั้งแล้วหรือไม่ (และแหล่งที่มา)"""
    web = stored(db)
    view = {}
    for name, (env_name, secret) in FIELDS.items():
        view[name] = {"value": "" if secret else web.get(name, ""), "source": source_of(name, web), "secret": secret,
                      "env_value": "" if secret else os.environ.get(env_name, "")}
    return view


def host_label(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""
