"""safety/views/master.py — หน้าจอข้อมูลหลักทุกตารางใน REGISTRY (ค้นหา/เรียง/แบ่งหน้า/เพิ่ม/แก้ไข/ปิดใช้งาน)"""
from __future__ import annotations

from flask import Blueprint, abort, g, redirect, render_template, request

from ..db import get_db
from ..master import REGISTRY, active_options, get_row, list_rows, parent_td, save_row, toggle_row
from ..perms import permission_required, user_permissions

bp = Blueprint("master", __name__, url_prefix="/master")


def _td(slug: str):
    td = REGISTRY.get(slug)
    if td is None:
        abort(404)
    return td


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.route("/<slug>")
@permission_required("master.view")
def index(slug):
    td, db = _td(slug), get_db()
    q, sort = request.args.get("q", ""), request.args.get("sort", "code")
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    page = list_rows(db, td, q, sort, direction, _int(request.args.get("page"), 1))
    return render_template("master_list.html", td=td, page=page, q=q, sort=sort, direction=direction,
                           can_edit="master.edit" in user_permissions(db, g.user))


def _form(td, row_id=None):
    db = get_db()
    row = get_row(db, td, row_id) if row_id is not None else None
    if row_id is not None and row is None:
        abort(404)
    errors: list[str] = []
    if request.method == "POST":
        errors = save_row(db, td, row_id, request.form, g.user["id"])
        if not errors:
            return redirect(f"/master/{td.slug}")
    values = request.form if request.method == "POST" else (row or {})
    parents, selected = [], None
    if td.parent:
        parents = list(active_options(db, parent_td(td)))
        selected = _int(values.get(td.parent.column), 0)
        if row is not None and selected and all(p["id"] != selected for p in parents):   # แม่เดิมที่ถูกปิดแล้ว ยังแสดงให้เห็น
            cur = db.execute(f"SELECT id, code, name_th FROM {td.parent.table} WHERE id=?", (selected,)).fetchone()
            parents = ([cur] if cur else []) + parents
    return render_template("master_form.html", td=td, row=row, values=values, errors=errors,
                           parents=parents, selected=selected)


@bp.route("/<slug>/new", methods=["GET", "POST"])
@permission_required("master.edit")
def new(slug):
    return _form(_td(slug))


@bp.route("/<slug>/<int:row_id>/edit", methods=["GET", "POST"])
@permission_required("master.edit")
def edit(slug, row_id):
    return _form(_td(slug), row_id)


@bp.route("/<slug>/<int:row_id>/toggle", methods=["POST"])
@permission_required("master.edit")
def toggle(slug, row_id):
    td, db = _td(slug), get_db()
    if get_row(db, td, row_id) is None:
        abort(404)
    toggle_row(db, td, row_id)
    return redirect(f"/master/{td.slug}")
