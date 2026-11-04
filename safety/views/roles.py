"""safety/views/roles.py — ประเภทผู้ใช้งาน (บทบาท + สิทธิ์) แก้ไขได้จากหน้าจอ"""
from __future__ import annotations

import re

from flask import Blueprint, flash, redirect, render_template, request

from ..db import get_db
from flask import g

from ..perms import PERMISSIONS, permission_required, role_permissions, user_permissions

bp = Blueprint("roles", __name__, url_prefix="/admin")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def _perms_from_form(form) -> str | None:
    chosen = form.getlist("permissions")
    if any(p not in PERMISSIONS for p in chosen):
        return None
    return ",".join(p for p in PERMISSIONS if p in chosen)       # เรียงตามลำดับมาตรฐาน ไม่ซ้ำ


@bp.route("/roles", methods=["GET", "POST"])
@permission_required("roles.manage")
def roles():
    db = get_db()
    if request.method == "POST":
        f = request.form
        action, key, name = f.get("action"), f.get("key", "").strip(), f.get("name_th", "").strip()
        row = db.execute("SELECT * FROM safety_roles WHERE key=?", (key,)).fetchone()
        actor = user_permissions(db, g.user)
        perms = _perms_from_form(f)
        asked = set(perms.split(",")) - {""} if perms else set()
        if action == "create":
            if perms is not None and not asked <= actor:
                flash("กำหนดสิทธิ์ที่คุณไม่มีให้บทบาทอื่นไม่ได้")
            elif not KEY_RE.match(key) or row is not None:
                flash("รหัสบทบาทต้องขึ้นต้นด้วย a-z ใช้ a-z 0-9 _ ยาว 2-32 ตัว และห้ามซ้ำกับที่มีอยู่")
            elif not name:
                flash("กรุณากรอกชื่อบทบาท")
            elif perms is None:
                flash("มีสิทธิ์ที่ไม่รู้จัก")
            else:
                db.execute("INSERT INTO safety_roles(key,name_th,permissions,is_system) VALUES(?,?,?,0)",
                           (key, name, perms))
                db.commit()
        elif row is None:
            flash("ไม่พบบทบาท")
        elif action in ("update", "delete") and not role_permissions(db, key) <= actor:
            flash("จัดการบทบาทที่มีสิทธิ์สูงกว่าคุณไม่ได้")
        elif action == "update":
            if perms is not None and not (asked - role_permissions(db, key)) <= actor:
                flash("กำหนดสิทธิ์ที่คุณไม่มีให้บทบาทอื่นไม่ได้")
            elif not name:
                flash("กรุณากรอกชื่อบทบาท")
            elif perms is None:
                flash("มีสิทธิ์ที่ไม่รู้จัก")
            elif key == "admin":                      # ผู้ดูแลระบบได้สิทธิ์ครบเสมอ แก้ได้เฉพาะชื่อ
                db.execute("UPDATE safety_roles SET name_th=? WHERE key='admin'", (name,))
                db.commit()
            else:
                db.execute("UPDATE safety_roles SET name_th=?, permissions=? WHERE key=?", (name, perms, key))
                db.commit()
        elif action == "delete":
            if row["is_system"]:
                flash("ลบบทบาทระบบไม่ได้")
            elif db.execute("SELECT 1 FROM safety_users WHERE role=? LIMIT 1", (key,)).fetchone():
                flash("มีผู้ใช้งานใช้บทบาทนี้อยู่ ลบไม่ได้")
            else:
                db.execute("DELETE FROM safety_roles WHERE key=?", (key,))
                db.commit()
        return redirect("/admin/roles")
    rows = db.execute("""SELECT r.*, (SELECT COUNT(*) FROM safety_users u WHERE u.role = r.key) AS users
                         FROM safety_roles r ORDER BY r.is_system DESC, r.key""").fetchall()
    return render_template("roles.html", roles=rows, permissions=PERMISSIONS)
