"""safety/views/api.py — API ภายใน: สรุปตัวเลขให้ Hub แสดงเป็นป้ายบนไทล์ (ใช้ service token)"""
from __future__ import annotations

import hmac

from flask import Blueprint, abort, current_app, jsonify, request

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/summary")
def summary():
    want = current_app.config["SERVICE_TOKEN"]
    sent = request.headers.get("X-Service-Token", "")
    if not want or not hmac.compare_digest(sent, want):
        abort(401)
    return jsonify({"open_hazards": 0, "overdue": 0})       # เฟส 3 จะนับจากทะเบียนอันตรายจริง
