"""safety/views/report_due.py — กำหนดส่งรายงาน (ผู้ดูแล) และปุ่ม "บันทึกว่าส่งรายงานแล้ว" จากหน้ารายงาน"""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .. import report_due as rd, reports, timeutil
from ..db import get_db
from ..perms import permission_required
from .hazards import _int

bp = Blueprint("report_due", __name__)


def _flash(errors, ok="บันทึกแล้ว"):
    for e in errors or [ok]:
        flash(e)


@bp.route("/admin/report-due", methods=["GET", "POST"])
@permission_required("report.manage")
def admin():
    db = get_db()
    if request.method == "POST":
        f, action = request.form, request.form.get("action")
        if action == "save":
            _flash(rd.save_rule(db, g.user, f))
        elif action == "toggle":
            _flash(rd.toggle_rule(db, g.user, _int(f.get("id")) or 0))
        elif action == "submit":
            _flash(rd.mark_submitted(db, g.user, f.get("report_key", ""), f.get("department_id"), f.get("period", ""), f.get("note", "")), "บันทึกว่าส่งรายงานแล้ว")
        else:
            flash("ไม่รองรับการดำเนินการนี้")
        return redirect("/admin/report-due")
    today = timeutil.today()
    rows = [{"rule": r, "status": rd.status(db, r, today),
             "dept": (db.execute("SELECT name_th FROM departments WHERE id=?", (r["department_id"],)).fetchone() or [None])[0]} for r in rd.rules(db, g.user)]
    departments = [d for d in db.execute("SELECT d.id, d.name_th, f.name_th AS factory FROM departments d JOIN factories f ON f.id = d.factory_id ORDER BY f.name_th, d.name_th")
                   if reports.department_in_scope(db, g.user, d["id"]) is not None]
    return render_template("report_due_admin.html", rows=rows, names=reports.REPORTS, frequencies=rd.FREQUENCY_TH, departments=departments,
                           history=rd.submissions(db, g.user), months=reports.MONTHS_TH, unscoped=rd._unscoped(g.user))


@bp.route("/reports/<key>/submit", methods=["POST"])
@permission_required("report.export")
def submit(key):
    if key not in reports.REPORTS:
        abort(404)
    f = request.form
    year, month = _int(f.get("year")) or timeutil.today().year, _int(f.get("month")) or timeutil.today().month
    dept = _int(f.get("department_id"))
    period = f"{year:04d}-{month:02d}" if key in ("monthly", "capa") else f"{year:04d}"
    _flash(rd.mark_submitted(get_db(), g.user, key, dept if key == "monthly" else None, period, f.get("note", "")), "บันทึกว่าส่งรายงานแล้ว")
    return redirect(f"/reports/{key}?year={year}&month={month}" + (f"&department_id={dept}" if dept else ""))
