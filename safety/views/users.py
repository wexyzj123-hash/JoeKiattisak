"""safety/views/users.py — ผู้ใช้งานของ Safety (บัญชีของ Safety เอง): สร้างบัญชี อนุมัติ/ปฏิเสธคำขอ ตั้งรหัสผ่านใหม่
กำหนดบทบาท ขอบเขตหน่วยงาน ตำแหน่ง ระดับ (L0-L2) และเปิด/ปิดใช้งาน"""
from __future__ import annotations

import sqlite3

from flask import Blueprint, flash, g, redirect, render_template, request

from shared.auth import MIN_PASSWORD, hash_password

from .. import accounts
from ..db import get_db

from ..perms import permission_required, role_permissions, user_permissions

bp = Blueprint("users", __name__, url_prefix="/admin")


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_OTHER_ACTIVE_ADMIN = ("EXISTS (SELECT 1 FROM safety_users o WHERE o.role='admin' AND o.active=1 AND o.id <> ?)")


def safe_set_role(db, user_id: int, role: str) -> bool:
    """เปลี่ยนบทบาทด้วยคำสั่งเดียวแบบ atomic: ห้ามทำให้ไม่เหลือผู้ดูแลระบบที่ใช้งานอยู่ (กันสองคนลดสิทธิ์กันพร้อมกัน)"""
    cur = db.execute(f"UPDATE safety_users SET role=? WHERE id=? AND (role <> 'admin' OR ? = 'admin' OR active = 0 OR {_OTHER_ACTIVE_ADMIN})",
                     (role, user_id, role, user_id))
    db.commit()
    return cur.rowcount == 1


def safe_disable(db, user_id: int) -> bool:
    cur = db.execute(f"UPDATE safety_users SET active=0 WHERE id=? AND (role <> 'admin' OR active = 0 OR {_OTHER_ACTIVE_ADMIN})",
                     (user_id, user_id))
    db.commit()
    return cur.rowcount == 1


def _set_scope(db, user, form) -> bool:
    raw_f, raw_d = (form.get("factory_id") or "").strip(), (form.get("department_id") or "").strip()
    fid, did = _int(raw_f), _int(raw_d)
    if (raw_f and fid is None) or (raw_d and did is None):
        return _no("ขอบเขตหน่วยงานไม่ถูกต้อง")
    if fid is not None:
        fac = db.execute("SELECT active FROM factories WHERE id=?", (fid,)).fetchone()
        if fac is None:
            return _no("ไม่พบโรงงานที่เลือก")
        if not fac["active"] and fid != user["factory_id"]:
            return _no("โรงงานที่เลือกถูกปิดใช้งานแล้ว")
    if did is not None:
        dept = db.execute("SELECT factory_id, active FROM departments WHERE id=?", (did,)).fetchone()
        if dept is None:
            return _no("ไม่พบแผนกที่เลือก")
        if not dept["active"] and did != user["department_id"]:
            return _no("แผนกที่เลือกถูกปิดใช้งานแล้ว")
        if fid is not None and dept["factory_id"] != fid:
            return _no("แผนกที่เลือกไม่ได้อยู่ในโรงงานนี้")
        fid = dept["factory_id"]                  # เลือกแผนกอย่างเดียว → ใช้โรงงานของแผนกนั้น
    db.execute("UPDATE safety_users SET factory_id=?, department_id=? WHERE id=?", (fid, did, user["id"]))
    db.commit()
    return True


def _no(message: str) -> bool:
    flash(message)
    return False


def _pick(db, table: str, raw, current=None):
    """id ของตำแหน่ง/ระดับที่เลือก (ว่าง = ไม่ระบุ); ต้องใช้งานอยู่ หรือเป็นค่าเดิมของผู้ใช้; ไม่ถูกต้อง → False"""
    if not str(raw or "").strip():
        return None
    ident = _int(raw)
    row = db.execute(f"SELECT id, active FROM {table} WHERE id=?", (ident,)).fetchone() if ident is not None else None
    if row is None or (not row["active"] and row["id"] != current):
        return False
    return row["id"]


def _can_grant(db, actor: set, role: str) -> bool:
    return db.execute("SELECT 1 FROM safety_roles WHERE key=?", (role,)).fetchone() is not None and role_permissions(db, role) <= actor


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
        if action == "create":
            _create(db, f, actor)
        elif action == "set_registration":
            db.execute("INSERT INTO app_settings(key, value) VALUES('self_register', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if f.get("open") == "1" else "0",))
            db.commit()
            flash("เปิดรับคำขอสร้างบัญชีด้วยตนเองแล้ว" if f.get("open") == "1" else "ปิดรับคำขอสร้างบัญชีด้วยตนเองแล้ว (คำขอที่ค้างอยู่ยังอนุมัติหรือปฏิเสธได้)")
        elif user is None:
            flash("ไม่พบผู้ใช้")
        elif action in ("approve", "reject"):
            _review(db, user, f, actor, action)
        elif user["pending"] and action in ("toggle", "set_role", "set_scope", "reset_password", "set_profile"):
            flash("บัญชีนี้รออนุมัติ — กดอนุมัติหรือปฏิเสธก่อน")
        elif action in ("reset_password", "set_profile") and not role_permissions(db, user["role"]) <= actor:
            flash("จัดการผู้ใช้ที่มีสิทธิ์สูงกว่าคุณไม่ได้")
        elif action == "reset_password":
            if len(f.get("password", "")) < MIN_PASSWORD:
                flash(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
            else:
                db.execute("UPDATE safety_users SET password_hash=? WHERE id=?", (hash_password(f["password"]), user["id"]))
                db.commit()
                flash(f"ตั้งรหัสผ่านใหม่ให้ {user['name']} แล้ว — แจ้งรหัสให้เจ้าของบัญชีโดยตรง")
        elif action == "set_profile":
            pos, lv = _pick(db, "positions", f.get("position_id"), user["position_id"]), _pick(db, "user_levels", f.get("level_id"), user["level_id"])
            if pos is False or lv is False:
                flash("ตำแหน่งหรือระดับที่เลือกไม่ถูกต้อง")
            else:
                db.execute("UPDATE safety_users SET position_id=?, level_id=? WHERE id=?", (pos, lv, user["id"]))
                db.commit()
        elif action in ("toggle", "set_role") and not role_permissions(db, user["role"]) <= actor:
            flash("จัดการผู้ใช้ที่มีสิทธิ์สูงกว่าคุณไม่ได้")           # เช่น ผู้ถือ users.manage แตะต้องผู้ดูแลระบบไม่ได้
        elif action == "set_role" and not role_permissions(db, f.get("role", "")) <= actor:
            flash("กำหนดบทบาทที่มีสิทธิ์มากกว่าที่คุณมีไม่ได้")
        elif action == "toggle":
            if user["active"]:
                if not safe_disable(db, user["id"]):
                    flash("ปิดใช้งานผู้ดูแลระบบคนสุดท้ายไม่ได้")
            else:
                db.execute("UPDATE safety_users SET active=1 WHERE id=?", (user["id"],))
                db.commit()
        elif action == "set_role":
            role = f.get("role", "")
            if db.execute("SELECT 1 FROM safety_roles WHERE key=?", (role,)).fetchone() is None:
                flash("ไม่พบบทบาทที่เลือก")
            elif not safe_set_role(db, user["id"], role):
                flash("ลดสิทธิ์ผู้ดูแลระบบคนสุดท้ายไม่ได้")
        elif action == "set_scope":
            _set_scope(db, user, f)
        return redirect("/admin/users")
    rows = db.execute("""SELECT u.*, r.name_th AS role_name, fa.name_th AS factory_name, d.name_th AS department_name
                         FROM safety_users u LEFT JOIN safety_roles r ON r.key = u.role
                         LEFT JOIN factories fa ON fa.id = u.factory_id LEFT JOIN departments d ON d.id = u.department_id
                         WHERE u.pending = 0 ORDER BY u.id""").fetchall()
    pending = db.execute("SELECT u.* FROM safety_users u WHERE u.pending = 1 ORDER BY u.id").fetchall()
    return render_template(
        "users.html", users=rows, pending=pending, reg_open=accounts.registration_open(db),
        positions=db.execute("SELECT id, name_th, active FROM positions ORDER BY id").fetchall(),
        levels=db.execute("SELECT id, name_th, active FROM user_levels ORDER BY code").fetchall(),
        roles=db.execute("SELECT key, name_th FROM safety_roles ORDER BY is_system DESC, key").fetchall(),
        factories=db.execute("SELECT id, name_th, active FROM factories ORDER BY code").fetchall(),
        departments=db.execute("""SELECT d.id, d.name_th, d.active, f.name_th AS factory FROM departments d
                                  JOIN factories f ON f.id = d.factory_id ORDER BY f.code, d.code""").fetchall())


def _create(db, f, actor: set) -> None:
    username, name = f.get("username", "").strip(), f.get("name", "").strip()
    role = f.get("role", "reporter")
    errors = accounts.validate_new(db, username, name, f.get("password", ""), None, f.get("email", "").strip())
    pos, lv = _pick(db, "positions", f.get("position_id")), _pick(db, "user_levels", f.get("level_id"))
    if pos is False or lv is False:
        errors.append("ตำแหน่งหรือระดับที่เลือกไม่ถูกต้อง")
    if not _can_grant(db, actor, role):
        errors.append("กำหนดบทบาทที่มีสิทธิ์มากกว่าที่คุณมีไม่ได้ (หรือไม่พบบทบาทที่เลือก)")
    if errors:
        for e in errors:
            flash(e)
        return
    try:
        uid = accounts.create_user(db, username=username, name=name, password=f["password"], role=role, level_id=lv, position_id=pos, email=f.get("email", "").strip())
    except sqlite3.IntegrityError:
        return flash("ชื่อผู้ใช้นี้ถูกใช้แล้ว กรุณาเลือกชื่ออื่น")
    user = db.execute("SELECT * FROM safety_users WHERE id=?", (uid,)).fetchone()
    if f.get("factory_id") or f.get("department_id"):
        _set_scope(db, user, f)
    flash(f"สร้างบัญชี {username} แล้ว — แจ้งชื่อผู้ใช้และรหัสผ่านให้เจ้าของบัญชีโดยตรง")


def _review(db, user, f, actor: set, action: str) -> None:
    if not user["pending"]:
        return flash("ดำเนินการได้เฉพาะคำขอที่รออนุมัติ")
    if action == "reject":
        db.execute("DELETE FROM safety_users WHERE id=? AND pending=1", (user["id"],))
        db.commit()
        return flash(f"ปฏิเสธและลบคำขอของ {user['name']} แล้ว")
    role = f.get("role", "reporter")
    if not _can_grant(db, actor, role):
        return flash("กำหนดบทบาทที่มีสิทธิ์มากกว่าที่คุณมีไม่ได้ (หรือไม่พบบทบาทที่เลือก)")
    pos, lv = _pick(db, "positions", f.get("position_id"), user["position_id"]), _pick(db, "user_levels", f.get("level_id"), user["level_id"])
    if pos is False or lv is False:
        return flash("ตำแหน่งหรือระดับที่เลือกไม่ถูกต้อง")
    if (f.get("factory_id") or f.get("department_id")) and not _set_scope(db, user, f):
        return
    db.execute("UPDATE safety_users SET active=1, pending=0, role=?, position_id=?, level_id=? WHERE id=? AND pending=1", (role, pos, lv, user["id"]))
    db.commit()
    flash(f"อนุมัติบัญชี {user['name']} แล้ว — แจ้งผู้ขอให้เข้าสู่ระบบด้วยชื่อผู้ใช้ {user['username']}")
