"""safety/security.py — CSRF + ตรวจสิทธิ์ของ Safety (ผู้ใช้ของ Safety เอง ไม่ใช่ของ Hub)"""
from __future__ import annotations

import hmac
import secrets
from functools import wraps

from flask import abort, current_app, g, redirect, request, session

CSRF_KEY = "_csrf"


def csrf_token() -> str:
    if CSRF_KEY not in session:
        session[CSRF_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_KEY]


def install_csrf(app) -> None:
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get(CSRF_KEY) or request.headers.get("X-CSRF-Token") or ""
            want = session.get(CSRF_KEY) or ""
            if not want or not hmac.compare_digest(sent, want):
                abort(400)


def load_user() -> None:
    from .db import get_db
    g.user = None
    suid = session.get("suid")
    if suid is None:
        return
    row = get_db().execute("SELECT * FROM safety_users WHERE id=?", (suid,)).fetchone()
    if row is None or not row["active"]:
        session.pop("suid", None)
        return
    g.user = row


def safety_login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.get("user") is None:
            return redirect(current_app.config["HUB_URL"])
        return view(*a, **kw)
    return wrapped


def safety_role_required(*roles):
    def deco(view):
        @wraps(view)
        @safety_login_required
        def wrapped(*a, **kw):
            if g.user["role"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco
