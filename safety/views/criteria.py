"""safety/views/criteria.py — เกณฑ์ความเสี่ยง (มาตราส่วนโอกาส/ความรุนแรง + ระดับความเสี่ยง) แก้ได้จากหน้าจอ"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, request

from ..db import get_db
from ..perms import permission_required
from ..risk import EXTRA_LEVEL_ROWS, MAX_SCALE_ROWS, apply_criteria, criteria_form_values, list_levels

bp = Blueprint("criteria", __name__, url_prefix="/admin")


@bp.route("/criteria", methods=["GET", "POST"])
@permission_required("criteria.manage")
def criteria():
    db = get_db()
    errors: list[str] = []
    if request.method == "POST":
        errors = apply_criteria(db, request.form)
        if not errors:
            return redirect("/admin/criteria")
    v = request.form if request.method == "POST" else criteria_form_values(db)
    try:
        rows = max(0, min(30, int(v.get("level_count", ""))))
    except ValueError:
        rows = len(list_levels(db)) + EXTRA_LEVEL_ROWS
    return render_template("criteria.html", v=v, errors=errors, level_rows=range(rows),
                           scale_rows=range(1, MAX_SCALE_ROWS + 1))
