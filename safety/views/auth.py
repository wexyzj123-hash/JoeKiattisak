"""safety/views/auth.py — เข้าสู่ระบบ Safety ด้วยบัญชีของ Safety เอง, ขอสร้างบัญชี (ผู้ดูแลอนุมัติ), ตั้งผู้ดูแลคนแรก"""
from __future__ import annotations

import json
import sqlite3

from flask import Blueprint, flash, g, redirect, render_template, request, session

from shared.auth import check_password, hash_password, safe_next

from .. import accounts, notify
from ..db import get_db
from ..loginguard import LoginGuard

bp = Blueprint("auth", __name__)
guard = LoginGuard()


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password("dummy-password-for-timing"))
    return _DUMMY[0]


def _ip() -> str:
    return request.remote_addr or "?"


def _org_lists(db) -> dict:
    f = [{"id": r["id"], "name": r["name_th"]} for r in db.execute("SELECT id, name_th FROM factories WHERE active=1 ORDER BY name_th")]
    d = [{"id": r["id"], "name": r["name_th"], "factory_id": r["factory_id"]} for r in db.execute(
        "SELECT d.id, d.name_th, d.factory_id FROM departments d JOIN factories f ON f.id = d.factory_id WHERE d.active=1 AND f.active=1 ORDER BY d.name_th")]
    l = [{"id": r["id"], "name": r["name_th"], "department_id": r["department_id"]} for r in db.execute(
        "SELECT l.id, l.name_th, l.department_id FROM lines l JOIN departments d ON d.id = l.department_id WHERE l.active=1 AND d.active=1 ORDER BY l.name_th")]
    return {"factories": f, "departments": d, "lines": l}


def _choices(db) -> dict:
    return {"levels": db.execute("SELECT id, name_th FROM user_levels WHERE active=1 ORDER BY code").fetchall(),
            "positions": db.execute("SELECT id, name_th FROM positions WHERE active=1 ORDER BY id").fetchall()}


@bp.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    nxt = safe_next(request.values.get("next"))
    if not accounts.admin_exists(db) and not db.execute("SELECT 1 FROM safety_users WHERE password_hash <> '' LIMIT 1").fetchone():
        return redirect("/setup")                                   # ยังไม่มีบัญชีที่ล็อกอินได้เลย → ตั้งผู้ดูแลคนแรก
    if g.get("user") is not None:
        return redirect(nxt)
    if request.method == "POST":
        username, password = request.form.get("username", "").strip(), request.form.get("password", "")
        wait = guard.locked_for(_ip(), username)
        if wait:
            flash(f"ลองผิดหลายครั้งเกินไป กรุณารออีกประมาณ {max(1, wait // 60)} นาทีแล้วลองใหม่")
            return render_template("login.html", next=nxt, can_register=accounts.registration_open(db)), 429
        row = db.execute("SELECT * FROM safety_users WHERE lower(username)=lower(?)", (username,)).fetchone() if username else None
        # ตรวจรหัสผ่านทุกครั้ง (แม้ไม่พบบัญชี) เพื่อให้เวลาตอบใกล้เคียงกัน ไม่บอกใบ้ว่าชื่อผู้ใช้มีอยู่หรือไม่
        stored = row["password_hash"] if row and row["password_hash"] else _dummy_hash()
        good = check_password(stored, password) and bool(row and row["password_hash"])
        if row and good:
            if row["pending"]:
                flash("บัญชีของคุณอยู่ระหว่างรอผู้ดูแลระบบอนุมัติ — เมื่ออนุมัติแล้วจึงเข้าสู่ระบบได้")
                return render_template("login.html", next=nxt, can_register=accounts.registration_open(db))
            if not row["active"]:
                flash("บัญชีนี้ถูกปิดใช้งาน กรุณาติดต่อผู้ดูแลระบบ")
                return render_template("login.html", next=nxt, can_register=accounts.registration_open(db))
            guard.ok(_ip(), username)
            session.clear()
            session.permanent = True                                # อายุตาม PERMANENT_SESSION_LIFETIME (8 ชั่วโมง)
            session["suid"] = row["id"]
            return redirect(nxt)
        guard.failed(_ip(), username)
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    return render_template("login.html", next=nxt, can_register=accounts.registration_open(db))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """ตั้งผู้ดูแลระบบคนแรก — ใช้ได้เฉพาะตอนที่ยังไม่มีผู้ดูแลที่ตั้งรหัสผ่านแล้ว"""
    db = get_db()
    if accounts.admin_exists(db):
        return redirect("/login")
    f = request.form
    if request.method == "POST":
        username, name = f.get("username", "").strip(), f.get("name", "").strip()
        errors = accounts.validate_new(db, username, name, f.get("password", ""), f.get("password2", ""))
        if errors:
            for e in errors:
                flash(e)
        else:
            uid = accounts.create_user(db, username=username, name=name, password=f["password"], role="admin")
            session.clear()
            session.permanent = True
            session["suid"] = uid
            return redirect("/")
    return render_template("setup.html", values=f)


@bp.route("/register", methods=["GET", "POST"])
def register():
    """ขอสร้างบัญชีเอง: ต้องระบุโรงงาน/แผนก/ตำแหน่ง/ระดับ แล้วรอผู้ดูแลอนุมัติ (บัญชีเริ่มเป็นรออนุมัติ เข้าระบบไม่ได้)"""
    db = get_db()
    if not accounts.admin_exists(db):
        return redirect("/setup")
    if not accounts.registration_open(db):
        return render_template("register_closed.html"), 403
    org, ch, f = _org_lists(db), _choices(db), request.form
    if request.method == "POST":
        username, name, email = f.get("username", "").strip(), f.get("name", "").strip(), f.get("email", "").strip()
        errors = accounts.validate_new(db, username, name, f.get("password", ""), f.get("password2", ""), email)
        fac = next((x for x in org["factories"] if str(x["id"]) == f.get("factory_id", "")), None)
        dep = next((x for x in org["departments"] if str(x["id"]) == f.get("department_id", "") and fac and x["factory_id"] == fac["id"]), None)
        line = next((x for x in org["lines"] if str(x["id"]) == f.get("line_id", "") and dep and x["department_id"] == dep["id"]), None)
        if fac is None or dep is None:
            errors.append("กรุณาเลือกโรงงานและแผนกที่คุณสังกัดให้ครบ (ไลน์เลือกหรือไม่ก็ได้)")
        if f.get("line_id", "") and line is None and dep is not None:
            errors.append("ไลน์ที่เลือกไม่อยู่ในแผนกนี้")
        pos = db.execute("SELECT id, name_th FROM positions WHERE id=? AND active=1", (f.get("position_id") or 0,)).fetchone()
        lv = db.execute("SELECT id, name_th FROM user_levels WHERE id=? AND active=1", (f.get("level_id") or 0,)).fetchone()
        if pos is None:
            errors.append("กรุณาเลือกตำแหน่ง")
        if lv is None:
            errors.append("กรุณาเลือกระดับ (L0/L1/L2)")
        if not errors and db.execute("SELECT COUNT(*) FROM safety_users WHERE pending=1").fetchone()[0] >= accounts.MAX_PENDING:
            errors.append("มีคำขอรออนุมัติมากเกินไปในขณะนี้ กรุณาติดต่อผู้ดูแลระบบโดยตรง")
        if errors:
            for e in errors:
                flash(e)
        else:
            org_text = " › ".join(x["name"] for x in (fac, dep, line) if x)
            try:
                uid = accounts.create_user(db, username=username, name=name, password=f["password"], role="reporter", active=0, pending=1,
                                           level_id=lv["id"], position_id=pos["id"], email=email, requested_org=org_text,
                                           req_factory_id=fac["id"], req_department_id=dep["id"])
            except sqlite3.IntegrityError:
                flash("ชื่อผู้ใช้นี้ถูกใช้แล้ว กรุณาเลือกชื่ออื่น")
            else:
                notify.emit_user_request(db, uid)
                db.commit()
                return render_template("register_done.html")
    return render_template("register.html", values=f, org=org, org_json=json.dumps(org, ensure_ascii=False), **ch)
