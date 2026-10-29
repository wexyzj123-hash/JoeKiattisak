"""hub/views/auth.py — ตั้งค่าผู้ดูแลคนแรก, เข้า/ออกจากระบบ"""
from __future__ import annotations

import re
import sqlite3

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


USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MAX_PENDING = 50                                  # กันการสมัครรัว ๆ ท่วมหน้าอนุมัติ


@bp.route("/register", methods=["GET", "POST"])
def register():
    """ขอสร้างบัญชีเอง: บัญชีเริ่มเป็น «รออนุมัติ» (ปิดใช้งาน บทบาทผู้ดูข้อมูล) จนกว่าผู้ดูแลระบบจะอนุมัติที่ ผู้ใช้ของ Hub"""
    if not get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect("/setup")
    f = request.form
    if request.method == "POST":
        db = get_db()
        username, name, pw = f.get("username", "").strip(), f.get("name_th", "").strip(), f.get("password", "")
        if not USERNAME_RE.match(username):
            flash("ชื่อผู้ใช้ต้องเป็นตัวอักษรอังกฤษ ตัวเลข . _ - ยาว 3-32 ตัวอักษร")
        elif not name or len(name) > 100:
            flash("กรุณากรอกชื่อ-นามสกุล (ไม่เกิน 100 ตัวอักษร)")
        elif len(pw) < MIN_PASSWORD:
            flash(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
        elif pw != f.get("password2", ""):
            flash("รหัสผ่านสองช่องไม่ตรงกัน")
        elif db.execute("SELECT COUNT(*) FROM users WHERE pending=1").fetchone()[0] >= MAX_PENDING:
            flash("มีคำขอรออนุมัติมากเกินไปในขณะนี้ กรุณาติดต่อผู้ดูแลระบบโดยตรง")
        else:
            try:
                db.execute("INSERT INTO users(username,name_th,password_hash,role,active,pending) VALUES(?,?,?,'viewer',0,1)",
                           (username, name, hash_password(pw)))
                db.commit()
            except sqlite3.IntegrityError:
                flash("ชื่อผู้ใช้นี้ถูกใช้แล้ว กรุณาเลือกชื่ออื่น")
            else:
                return render_template("register_done.html")
    return render_template("register.html", values=f)


@bp.route("/logout", methods=["GET", "POST"])   # GET: Safety ส่งผู้ใช้มาออกจากระบบกลาง
def logout():
    session.clear()
    safety = get_db().execute("SELECT url FROM app_registry WHERE key='safety' AND enabled=1").fetchone()
    # ออกจาก Safety ด้วย (Safety ล้าง session ของตัวเองแล้วส่งกลับ /login ของ Hub)
    return redirect(safety["url"].rstrip("/") + "/signout" if safety else "/login")
