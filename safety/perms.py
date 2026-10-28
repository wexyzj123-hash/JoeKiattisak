"""safety/perms.py — คีย์สิทธิ์ของ Safety (ตัวตรวจสิทธิ์เพิ่มใน Task 2)"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "master.view": "ดูข้อมูลหลัก",
    "master.edit": "แก้ไขข้อมูลหลัก",
    "users.manage": "จัดการผู้ใช้งาน",
    "roles.manage": "จัดการประเภทผู้ใช้งาน",
    "hazard.report": "แจ้ง/บันทึกอันตราย",
    "hazard.assess": "ประเมินความเสี่ยง",
    "hazard.assign": "มอบหมายผู้รับผิดชอบ",
    "hazard.close": "ปิดรายการอันตราย",
    "criteria.manage": "จัดการเกณฑ์ความเสี่ยง",
}


from functools import wraps  # noqa: E402

from flask import abort, current_app, g, redirect  # noqa: E402


def user_permissions(db, user) -> set[str]:
    """สิทธิ์ของผู้ใช้จากบทบาท; admin ได้ครบทุกสิทธิ์เสมอ (แก้ในตารางก็ไม่ลด); บทบาทที่ไม่มีอยู่ = ไม่มีสิทธิ์"""
    if user is None:
        return set()
    if user["role"] == "admin":
        return set(PERMISSIONS)
    row = db.execute("SELECT permissions FROM safety_roles WHERE key=?", (user["role"],)).fetchone()
    return {p for p in (row["permissions"].split(",") if row else []) if p in PERMISSIONS}


def permission_required(*keys: str):
    """ต้องล็อกอิน (ไม่ล็อกอิน → กลับ Hub) และมีสิทธิ์ครบทุกคีย์ (ไม่ครบ → 403)"""
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.get("user") is None:
                return redirect(current_app.config["HUB_URL"])
            from .db import get_db
            have = user_permissions(get_db(), g.user)
            if not all(k in have for k in keys):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco
