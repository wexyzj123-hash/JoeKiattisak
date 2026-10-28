"""hub/views/auth.py — ตั้งค่าผู้ดูแลคนแรก, เข้า/ออกจากระบบ"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session

from ..db import get_db
from ..security import MIN_PASSWORD, check_password, hash_password, safe_next

bp = Blueprint("auth", __name__)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        abort(404)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < MIN_PASSWORD:
            flash(f"กรุณากรอกชื่อผู้ใช้ และรหัสผ่านอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
        else:
            db.execute("INSERT INTO users(username,name_th,password_hash,role) VALUES(?,?,?,'admin')",
                       (username, request.form.get("name_th", "").strip() or username, hash_password(password)))
            db.commit()
            return redirect("/login")
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.args.get("next"))
    if request.method == "POST":
        row = get_db().execute("SELECT * FROM users WHERE username=?",
                               (request.form.get("username", "").strip(),)).fetchone()
        if row and row["active"] and check_password(row["password_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = row["id"]
            return redirect(nxt)
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    return render_template("login.html", next=nxt)


@bp.route("/logout", methods=["GET", "POST"])   # GET: Safety ส่งผู้ใช้มาออกจากระบบกลาง
def logout():
    session.clear()
    return redirect("/login")
