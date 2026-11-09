"""safety/views/drawer.py — ชิ้นส่วน HTML ของลิ้นชักรายละเอียด (เรียกจากแดชบอร์ดด้วย JS): ไม่มีสิทธิ์/ไม่พบ = 404"""
from __future__ import annotations

from flask import Blueprint, g, render_template

from .. import drawer
from ..db import get_db
from ..security import safety_login_required

bp = Blueprint("drawer", __name__)


@bp.route("/drawer/<kind>/<int:ident>")
@safety_login_required
def show(kind, ident):
    d = drawer.build(get_db(), g.user, kind, ident)
    if d is None:
        return '<div class="drawer-body"><h2 id="drawer-title">ไม่พบรายการ</h2><p class="drawer-sub">รายการนี้ไม่มีอยู่หรือคุณไม่มีสิทธิ์เปิดดู</p></div>', 404
    resp = render_template("drawer.html", d=d)
    return resp, 200, {"Cache-Control": "no-store"}
