"""hub/views/auth.py — ตั้งค่าผู้ดูแลคนแรก, เข้า/ออกจากระบบ"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, session

from ..db import get_db
from shared.urls import public_url
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
        if row and check_password(row["password_hash"], request.form.get("password", "")):
            if row["pending"]:
                flash("บัญชีของคุณอยู่ระหว่างรอผู้ดูแลระบบอนุมัติ — เมื่ออนุมัติแล้วจึงเข้าสู่ระบบได้")
                return render_template("login.html", next=nxt)
            if row["active"]:
                session.clear()
                session["uid"] = row["id"]
                return redirect(nxt)
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    return render_template("login.html", next=nxt)


@bp.route("/logout", methods=["GET", "POST"])   # GET: Safety ส่งผู้ใช้มาออกจากระบบกลาง
def logout():
    """ล้าง session ของ Hub แล้วแสดงหน้าสั้น ๆ ที่สั่ง Safety ล้าง session ของตัวเองเบื้องหลัง จากนั้นกลับหน้า Hub (เข้าดูได้โดยไม่ต้องล็อกอิน) เสมอ
    (ไม่ redirect ไปหา Safety ตรง ๆ เพราะถ้า Safety ปิดอยู่หรือที่อยู่เข้าไม่ถึง ผู้ใช้จะค้างหน้าผิดพลาดของเบราว์เซอร์)"""
    session.clear()
    safety = get_db().execute("SELECT url FROM app_registry WHERE key='safety' AND enabled=1").fetchone()
    target = public_url(safety["url"].rstrip("/"), request.host) + "/signout?quiet=1" if safety else ""
    resp = make_response(render_template("logout.html", signout=target))
    resp.headers["Cache-Control"] = "no-store"
    return resp
