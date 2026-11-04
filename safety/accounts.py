"""safety/accounts.py — บัญชีผู้ใช้ของ Safety เอง (ชื่อผู้ใช้ + รหัสผ่าน, ขอสร้างเอง → ผู้ดูแลอนุมัติ)"""
from __future__ import annotations

import re
import sqlite3

from shared.auth import MIN_PASSWORD, hash_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MAX_PENDING = 50                                                      # กันการขอบัญชีรัว ๆ ท่วมหน้าอนุมัติ
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


def registration_open(db) -> bool:
    row = db.execute("SELECT value FROM app_settings WHERE key='self_register'").fetchone()
    return (row[0] if row else "1") == "1"


def admin_exists(db) -> bool:
    """มีผู้ดูแลระบบที่ใช้งานอยู่และตั้งรหัสผ่านแล้วอย่างน้อยหนึ่งคนหรือยัง (ยังไม่มี → หน้า /setup สร้างคนแรก)"""
    return db.execute("SELECT 1 FROM safety_users WHERE role='admin' AND active=1 AND password_hash <> '' LIMIT 1").fetchone() is not None


def next_native_uid(db) -> int:
    """hub_uid ของบัญชีที่สร้างใน Safety เอง = ค่าติดลบไม่ซ้ำ (คอลัมน์เดิมบังคับไม่ซ้ำ; ค่าบวกคือบัญชีที่มาจาก SSO ของ Hub รุ่นเก่า)"""
    return min(db.execute("SELECT COALESCE(MIN(hub_uid), 0) FROM safety_users").fetchone()[0], 0) - 1


def validate_new(db, username: str, name: str, password: str, password2: str | None = None, email: str = "") -> list[str]:
    errors = []
    if not USERNAME_RE.match(username or ""):
        errors.append("ชื่อผู้ใช้ต้องเป็นตัวอักษรอังกฤษ ตัวเลข . _ - ยาว 3-32 ตัวอักษร")
    elif db.execute("SELECT 1 FROM safety_users WHERE lower(username)=lower(?)", (username,)).fetchone():
        errors.append("ชื่อผู้ใช้นี้ถูกใช้แล้ว กรุณาเลือกชื่ออื่น")
    if not (name or "").strip() or len(name.strip()) > 100:
        errors.append("กรุณากรอกชื่อ-นามสกุล (ไม่เกิน 100 ตัวอักษร)")
    if len(password or "") < MIN_PASSWORD:
        errors.append(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
    elif password2 is not None and password != password2:
        errors.append("รหัสผ่านสองช่องไม่ตรงกัน")
    if email and not EMAIL_RE.match(email):
        errors.append("รูปแบบอีเมลไม่ถูกต้อง")
    return errors


def create_user(db, *, username: str, name: str, password: str, role: str = "reporter", active: int = 1, pending: int = 0,
                level_id=None, position_id=None, email: str = "", requested_org: str = "", req_factory_id=None, req_department_id=None) -> int:
    for _ in range(3):
        try:
            cur = db.execute(
                "INSERT INTO safety_users(hub_uid, name, role, active, pending, username, password_hash, level_id, position_id, email, requested_org, "
                "req_factory_id, req_department_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (next_native_uid(db), name.strip(), role, active, pending, username, hash_password(password), level_id, position_id, email or None,
                 requested_org, req_factory_id, req_department_id))
            db.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if "username" in str(exc).lower() or "uq_safety_username" in str(exc):
                raise
    raise sqlite3.IntegrityError("create_user")
