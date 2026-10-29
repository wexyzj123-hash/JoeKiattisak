"""hub/security.py — รหัสผ่าน, CSRF, ตรวจสิทธิ์ (แบบเดียวกับ ecs/security.py)"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit
from functools import wraps

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

CSRF_KEY = "_csrf"
MIN_PASSWORD = 8
ROLE_TH = {"admin": "ผู้ดูแลระบบ", "supervisor": "หัวหน้างาน", "technician": "ช่างผู้ตรวจ", "viewer": "ผู้ดูข้อมูล"}


def hash_password(password: str) -> str:
    return generate_password_hash(password or "", method="pbkdf2:sha256:600000", salt_length=16)


def check_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password or "")


def csrf_token() -> str:
    if CSRF_KEY not in session:
        session[CSRF_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_KEY]


def install_csrf(app) -> None:
    """บังคับ CSRF กับทุกคำขอที่เปลี่ยนข้อมูล (POST/PUT/PATCH/DELETE) และให้ {{ csrf_token() }} ใช้ใน template"""
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get(CSRF_KEY) or request.headers.get("X-CSRF-Token") or ""
            want = session.get(CSRF_KEY) or ""
            if not want or not hmac.compare_digest(sent, want):
                abort(400)


def safe_next(target: str | None) -> str:
    """รับเฉพาะ path ภายในเว็บ — กัน open redirect"""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in target):     # tab/newline/space/NUL — เบราว์เซอร์ตัดทิ้งแล้วกลายเป็น //host
        return "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return "/"
    return target


def load_user() -> None:
    """โหลดผู้ใช้จากฐานข้อมูลทุกคำขอ — ผู้ใช้ที่ถูกปิดใช้งานหลังล็อกอินจะหลุดทันที"""
    from .db import get_db
    g.user = None
    uid = session.get("uid")
    if uid is None:
        return
    row = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if row is None or not row["active"]:
        session.pop("uid", None)
        return
    g.user = row


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.get("user") is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def role_required(*roles):
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if g.user["role"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco
