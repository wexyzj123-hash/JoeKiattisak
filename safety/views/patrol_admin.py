"""safety/views/patrol_admin.py — หน้าแอดมินแบบตรวจ/รายการตรวจ/แผนการตรวจ"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request

from .. import patrol_admin as pa
from ..db import get_db
from ..hazards import risk_area_options
from ..perms import permission_required, user_permissions
from flask import g

bp = Blueprint("patrol_admin", __name__, url_prefix="/admin")


def _int(raw):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _flash(errors):
    for e in errors:
        flash(e)


@bp.route("/checklists", methods=["GET", "POST"])
@permission_required("patrol.manage")
def checklists():
    db = get_db()
    if request.method == "POST":
        f, action = request.form, request.form.get("action")
        if action == "create":
            cid, errors = pa.create_checklist(db, g.user, f)
            _flash(errors)
            if cid:
                return redirect(f"/admin/checklists/{cid}")
        elif action == "toggle":
            _flash(pa.toggle_checklist(db, g.user, _int(f.get("id")) or 0))
        else:
            flash("ไม่รองรับการดำเนินการนี้ (แบบตรวจลบไม่ได้ ใช้การปิดใช้งาน)")
        return redirect("/admin/checklists")
    rows = db.execute("SELECT c.*, (SELECT COUNT(*) FROM checklist_items i WHERE i.checklist_id = c.id AND i.active=1) AS items "
                      "FROM checklists c ORDER BY c.active DESC, c.code").fetchall()
    return render_template("checklists.html", rows=rows)


@bp.route("/checklists/<int:checklist_id>", methods=["GET", "POST"])
@permission_required("patrol.manage")
def checklist(checklist_id):
    db = get_db()
    row = db.execute("SELECT * FROM checklists WHERE id=?", (checklist_id,)).fetchone()
    if row is None:
        abort(404)
    if request.method == "POST":
        f, action = request.form, request.form.get("action")
        if action == "update_checklist":
            _flash(pa.update_checklist(db, g.user, checklist_id, f))
        elif action == "toggle_checklist":
            _flash(pa.toggle_checklist(db, g.user, checklist_id))
        elif action == "add_item":
            _flash(pa.add_item(db, g.user, checklist_id, f)[1])
        elif action in ("update_item", "toggle_item"):
            item = db.execute("SELECT id FROM checklist_items WHERE id=? AND checklist_id=?", (_int(f.get("id")), checklist_id)).fetchone()
            if item is None:
                flash("ไม่พบรายการตรวจ")
            else:
                _flash(pa.update_item(db, g.user, item["id"], f) if action == "update_item" else pa.toggle_item(db, g.user, item["id"]))
        else:
            flash("ไม่รองรับการดำเนินการนี้ (รายการตรวจลบไม่ได้ ใช้การปิดใช้งาน)")
        return redirect(f"/admin/checklists/{checklist_id}")
    items = db.execute("SELECT * FROM checklist_items WHERE checklist_id=? ORDER BY sort, id", (checklist_id,)).fetchall()
    return render_template("checklist_items.html", c=row, items=items)


@bp.route("/patrol-plans", methods=["GET", "POST"])
@permission_required("patrol.manage")
def plans():
    db = get_db()
    if request.method == "POST":
        f, action = request.form, request.form.get("action")
        if action == "create":
            _flash(pa.create_plan(db, g.user, f)[1])
        elif action == "update":
            _flash(pa.update_plan(db, g.user, _int(f.get("id")) or 0, f))
        elif action == "toggle":
            _flash(pa.toggle_plan(db, g.user, _int(f.get("id")) or 0))
        else:
            flash("ไม่รองรับการดำเนินการนี้ (แผนลบไม่ได้ ใช้การปิดใช้งาน)")
        return redirect("/admin/patrol-plans")
    rows = db.execute("SELECT p.*, c.name_th AS checklist_name, a.name_th AS area_name FROM patrol_plans p JOIN checklists c ON c.id = p.checklist_id "
                      "JOIN risk_areas a ON a.id = p.risk_area_id ORDER BY p.active DESC, c.code, a.name_th").fetchall()
    inspectors = [u for u in db.execute("SELECT u.id, u.name FROM safety_users u WHERE u.active=1 ORDER BY u.name")
                  if "patrol.perform" in user_permissions(db, db.execute("SELECT * FROM safety_users WHERE id=?", (u["id"],)).fetchone())]
    return render_template("patrol_plans.html", rows=rows, frequencies=pa.FREQUENCIES, inspectors=inspectors, areas=risk_area_options(db, g.user),
                           checklists=db.execute("SELECT id, code, name_th FROM checklists WHERE active=1 ORDER BY code").fetchall())
