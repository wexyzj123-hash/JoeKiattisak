"""hub/views/api.py — API ภายในให้ระบบลูกตรวจสถานะผู้ใช้ (ใช้ service token ไม่ใช่ session)"""
from __future__ import annotations

import hmac

from flask import Blueprint, abort, current_app, jsonify, request

from ..db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/user-status/<int:uid>")
def user_status(uid: int):
    want = current_app.config["SERVICE_TOKEN"]
    if not want or not hmac.compare_digest(request.headers.get("X-Service-Token", ""), want):
        abort(401)
    row = get_db().execute("SELECT active, role FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        abort(404)
    resp = jsonify({"active": bool(row["active"]), "role": row["role"]})
    resp.headers["Cache-Control"] = "no-store"
    return resp
