"""safety/views/users.py — ผู้ใช้งานของ Safety: กำหนดบทบาท ขอบเขตหน่วยงาน และเปิด/ปิดใช้งาน
ผู้ใช้ปรากฏในรายการหลังเข้าสู่ระบบผ่าน Hub ครั้งแรกเท่านั้น (ไม่มีฟอร์มสร้างผู้ใช้)"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request

from ..db import get_db
from flask import g

from ..perms import permission_required, role_permissions, user_permissions

bp = Blueprint("users", __name__, url_prefix="/admin")


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _other_active_admins(db, user_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM safety_users WHERE role='admin' AND active=1 AND id<>?",
                      (user_id,)).fetchone()[0]


def _set_scope(db, user, form) -> None:
    raw_f, raw_d = (form.get("factory_id") or "").strip(), (form.get("department_id") or "").strip()
    fid, did = _int(raw_f), _int(raw_d)
    if (raw_f and fid is None) or (raw_d and did is None):
        return flash("ขอบเขตหน่วยงานไม่ถูกต้อง")
    if fid is not None:
        fac = db.execute("SELECT active FROM factories WHERE id=?", (fid,)).fetchone()
        if fac is None:
            return flash("ไม่พบโรงงานที่เลือก")
        if not fac["active"] and fid != user["factory_id"]:
            return flash("โรงงานที่เลือกถูกปิดใช้งานแล้ว")
    if did is not None:
        dept = db.execute("SELECT factory_id, active FROM departments WHERE id=?", (did,)).fetchone()
        if dept is None:
            return flash("ไม่พบแผนกที่เลือก")
        if not dept["active"] and did != user["department_id"]:
            return flash("แผนกที่เลือกถูกปิดใช้งานแล้ว")
        if fid is not None and dept["factory_id"] != fid:
            return flash("แผนกที่เลือกไม่ได้อยู่ในโรงงานนี้")
        fid = dept["factory_id"]                  # เลือกแผนกอย่างเดียว → ใช้โรงงานของแผนกนั้น
    db.execute("UPDATE safety_users SET factory_id=?, department_id=? WHERE id=?", (fid, did, user["id"]))
    db.commit()


@bp.route("/users", methods=["GET", "POST"])
@permission_required("users.manage")
def users():
    db = get_db()
    if request.method == "POST":
        f = request.form
        uid = _int(f.get("id"))
        user = db.execute("SELECT * FROM safety_users WHERE id=?", (uid,)).fetchone() if uid else None
        action = f.get("action")
        actor = user_permissions(db, g.user)
        if user is None:
            flash("ไม่พบผู้ใช้")
        elif action in ("toggle", "set_role") and not role_permissions(db, user["role"]) <= actor:
            flash("จัดการผู้ใช้ที่มีสิทธิ์สูงกว่าคุณไม่ได้")           # เช่น ผู้ถือ users.manage แตะต้องผู้ดูแลระบบไม่ได้
        elif action == "set_role" and not role_permissions(db, f.get("role", "")) <= actor:
            flash("กำหนดบทบาทที่มีสิทธิ์มากกว่าที่คุณมีไม่ได้")
        elif action == "toggle":
            if user["active"] and user["role"] == "admin" and _other_active_admins(db, user["id"]) == 0:
                flash("ปิดใช้งานผู้ดูแลระบบคนสุดท้ายไม่ได้")
            else:
                db.execute("UPDATE safety_users SET active = 1 - active WHERE id=?", (user["id"],))
                db.commit()
        elif action == "set_role":
            role = f.get("role", "")
            if db.execute("SELECT 1 FROM safety_roles WHERE key=?", (role,)).fetchone() is None:
                flash("ไม่พบบทบาทที่เลือก")
            elif user["role"] == "admin" and role != "admin" and user["active"] \
                    and _other_active_admins(db, user["id"]) == 0:
                flash("ลดสิทธิ์ผู้ดูแลระบบคนสุดท้ายไม่ได้")
            else:
                db.execute("UPDATE safety_users SET role=? WHERE id=?", (role, user["id"]))
                db.commit()
        elif action == "set_scope":
            _set_scope(db, user, f)
        return redirect("/admin/users")
    rows = db.execute("""SELECT u.*, r.name_th AS role_name, fa.name_th AS factory_name, d.name_th AS department_name
                         FROM safety_users u LEFT JOIN safety_roles r ON r.key = u.role
                         LEFT JOIN factories fa ON fa.id = u.factory_id LEFT JOIN departments d ON d.id = u.department_id
                         ORDER BY u.id""").fetchall()
    return render_template(
        "users.html", users=rows,
        roles=db.execute("SELECT key, name_th FROM safety_roles ORDER BY is_system DESC, key").fetchall(),
        factories=db.execute("SELECT id, name_th, active FROM factories ORDER BY code").fetchall(),
        departments=db.execute("""SELECT d.id, d.name_th, d.active, f.name_th AS factory FROM departments d
                                  JOIN factories f ON f.id = d.factory_id ORDER BY f.code, d.code""").fetchall())
