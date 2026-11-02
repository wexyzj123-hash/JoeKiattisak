"""shared/auth.py — ส่วนที่ Hub และ Safety ใช้ร่วมกัน: แฮชรหัสผ่าน, กฎรหัสผ่าน, กรองที่อยู่ปลายทางหลังล็อกอิน (กัน open redirect)"""
from __future__ import annotations

from urllib.parse import urlsplit

from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD = 8


def hash_password(password: str) -> str:
    return generate_password_hash(password or "", method="pbkdf2:sha256:600000", salt_length=16)


def check_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False                                          # บัญชีที่ยังไม่เคยตั้งรหัสผ่าน (เช่น สร้างจาก SSO เดิม) ล็อกอินด้วยรหัสว่างไม่ได้
    return check_password_hash(password_hash, password or "")


def safe_next(target: str | None) -> str:
    """รับเฉพาะ path ภายในเว็บ — กัน open redirect"""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in target):     # tab/newline/space/NUL — เบราว์เซอร์ตัดทิ้งแล้วกลายเป็น //host
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return "/"
    return target
