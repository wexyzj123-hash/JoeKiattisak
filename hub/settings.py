"""hub/settings.py — ค่าตั้งค่าของ Hub ที่ผู้ดูแลเปลี่ยนจากหน้าจอได้ (ตาราง settings)"""
from __future__ import annotations


def get_setting(db, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(db, key: str, value: str) -> None:
    db.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db.commit()


def registration_open(db) -> bool:
    """เปิดให้ผู้ใช้ขอสร้างบัญชีเองหรือไม่ (ค่าตั้งต้น: เปิด)"""
    return get_setting(db, "self_register", "1") == "1"
