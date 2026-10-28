"""safety/views/search.py — ช่องค้นหาบนแถบบน: ค้นรายการอันตรายตามรหัส/หัวข้อ"""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from ..db import get_db
from ..hazards import STATUS_TH
from ..perms import permission_any
from ..search import clean_query, search_hazards

bp = Blueprint("search", __name__)


@bp.route("/search")
@permission_any("hazard.report", "hazard.assess", "hazard.assign", "hazard.close", "master.view")
def index():
    q = clean_query(request.args.get("q", ""))
    rows = search_hazards(get_db(), g.user, q) if q else []
    return render_template("search.html", q=q, rows=rows, status_th=STATUS_TH)
