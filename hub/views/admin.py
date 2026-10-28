"""hub/views/admin.py — จัดการทะเบียนระบบ (app_registry) และผู้ใช้ของ Hub (เฉพาะผู้ดูแลระบบ)"""
from __future__ import annotations

import re
import sqlite3

from flask import Blueprint, flash, g, redirect, render_template, request

from ..db import get_db
from ..security import MIN_PASSWORD, ROLE_TH, hash_password, role_required

bp = Blueprint("admin", __name__, url_prefix="/admin")
KEY_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


def _active_admin_count(db, excluding: int | None = None) -> int:
    return db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id<>?",
                      (excluding or 0,)).fetchone()[0]


@bp.route("/apps", methods=["GET", "POST"])
@role_required("admin")
def apps():
    db = get_db()
    if request.method == "POST":
        f = request.form
        key = f.get("key", "").strip()
        if f.get("action") == "toggle":
            db.execute("UPDATE app_registry SET enabled = 1 - enabled WHERE key=?", (key,))
            db.commit()
        else:
            name, url, role = f.get("name_th", "").strip(), f.get("url", "").strip(), f.get("required_role", "")
            try:
                sort = int(f.get("sort") or 0)
            except ValueError:
                sort = 0
            if not KEY_RE.match(key):
                flash("รหัสระบบใช้ได้เฉพาะ a-z 0-9 _ - ความยาว 2-32 ตัวอักษร")
            elif not name:
                flash("กรุณากรอกชื่อระบบ")
            elif not re.match(r"^https?://\S+$", url):
                flash("ที่อยู่ระบบต้องขึ้นต้นด้วย http:// หรือ https://")
            elif role not in ("",) + tuple(ROLE_TH):
                flash("บทบาทที่จำกัดสิทธิ์ไม่ถูกต้อง")
            else:
                db.execute("""INSERT INTO app_registry(key,name_th,url,icon,required_role,sort) VALUES(?,?,?,?,?,?)
                              ON CONFLICT(key) DO UPDATE SET name_th=excluded.name_th, url=excluded.url,
                              icon=excluded.icon, required_role=excluded.required_role, sort=excluded.sort""",
                           (key, name, url, f.get("icon", "app").strip() or "app", role, sort))
                db.commit()
        return redirect("/admin/apps")
    rows = db.execute("SELECT * FROM app_registry ORDER BY sort, id").fetchall()
    return render_template("admin_apps.html", apps=rows, roles=ROLE_TH)


@bp.route("/users", methods=["GET", "POST"])
@role_required("admin")
def users():
    db = get_db()
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        try:
            uid = int(f.get("id") or 0)
        except ValueError:
            uid = 0
        target = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
        if action == "create":
            username, role, password = f.get("username", "").strip(), f.get("role", ""), f.get("password", "")
            if not username or role not in ROLE_TH or len(password) < MIN_PASSWORD:
                flash(f"กรอกชื่อผู้ใช้ เลือกบทบาท และรหัสผ่านอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
            else:
                try:
                    db.execute("INSERT INTO users(username,name_th,password_hash,role) VALUES(?,?,?,?)",
                               (username, f.get("name_th", "").strip() or username, hash_password(password), role))
                    db.commit()
                except sqlite3.IntegrityError:
                    flash("ชื่อผู้ใช้นี้มีอยู่แล้ว")
        elif target is None:
            flash("ไม่พบผู้ใช้")
        elif action == "toggle":
            if target["active"] and target["role"] == "admin" and _active_admin_count(db, target["id"]) == 0:
                flash("ปิดใช้งานผู้ดูแลระบบคนสุดท้ายไม่ได้")
            else:
                db.execute("UPDATE users SET active = 1 - active WHERE id=?", (uid,))
                db.commit()
        elif action == "set_role":
            role = f.get("role", "")
            if role not in ROLE_TH:
                flash("บทบาทไม่ถูกต้อง")
            elif target["role"] == "admin" and role != "admin" and target["active"] \
                    and _active_admin_count(db, target["id"]) == 0:
                flash("ลดสิทธิ์ผู้ดูแลระบบคนสุดท้ายไม่ได้")
            else:
                db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
                db.commit()
        elif action == "reset_password":
            if len(f.get("password", "")) < MIN_PASSWORD:
                flash(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
            else:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(f["password"]), uid))
                db.commit()
        return redirect("/admin/users")
    rows = db.execute("SELECT * FROM users ORDER BY id").fetchall()
    return render_template("admin_users.html", users=rows, roles=ROLE_TH)
