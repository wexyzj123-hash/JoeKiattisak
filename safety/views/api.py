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


@bp.route("/org")
def org():
    """โครงสร้างองค์กรที่ใช้งานอยู่ (โรงงาน › แผนก › ไลน์) ให้ Hub ทำฟอร์มขอสร้างบัญชี — เฉพาะชื่อ ไม่มีข้อมูลบุคคล"""
    want = current_app.config["SERVICE_TOKEN"]
    if not want or not hmac.compare_digest(request.headers.get("X-Service-Token", ""), want):
        abort(401)
    db = get_db()
    factories = [dict(id=r["id"], name=r["name_th"]) for r in db.execute("SELECT id, name_th FROM factories WHERE active=1 ORDER BY name_th")]
    departments = [dict(id=r["id"], name=r["name_th"], factory_id=r["factory_id"]) for r in db.execute(
        "SELECT d.id, d.name_th, d.factory_id FROM departments d JOIN factories f ON f.id = d.factory_id WHERE d.active=1 AND f.active=1 ORDER BY d.name_th")]
    lines = [dict(id=r["id"], name=r["name_th"], department_id=r["department_id"]) for r in db.execute(
        "SELECT l.id, l.name_th, l.department_id FROM lines l JOIN departments d ON d.id = l.department_id WHERE l.active=1 AND d.active=1 ORDER BY l.name_th")]
    resp = jsonify({"factories": factories, "departments": departments, "lines": lines})
    resp.headers["Cache-Control"] = "no-store"
    return resp
