"""safety/views/api.py — API ภายใน: สรุปตัวเลขให้ Hub แสดงเป็นป้ายบนไทล์ (ใช้ service token)"""
from __future__ import annotations

import hmac

from flask import Blueprint, abort, current_app, jsonify, request

from .. import stats
from ..db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/summary")
def summary():
    want = current_app.config["SERVICE_TOKEN"]
    sent = request.headers.get("X-Service-Token", "")
    if not want or not hmac.compare_digest(sent, want):
        abort(401)
    open_hazards, overdue = stats.global_summary(get_db())
    return jsonify({"open_hazards": open_hazards, "overdue": overdue})
