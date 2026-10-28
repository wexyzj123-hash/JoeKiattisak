"""safety/views/reports.py — รายงานและสถิติ: หน้ารายงาน + ส่งออก Excel/PDF (ตามขอบเขตโรงงาน/แผนกของผู้ใช้)"""
from __future__ import annotations

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request

from .. import report_export, report_fonts, reports, timeutil
from ..db import get_db
from ..perms import permission_required, user_permissions
from .hazards import _int

bp = Blueprint("reports", __name__)


def _period() -> tuple[int, int, int | None]:
    today = timeutil.today()
    year, month = _int(request.args.get("year")) or today.year, _int(request.args.get("month")) or today.month
    dept = _int(request.args.get("department_id"))
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        flash("ปีหรือเดือนไม่ถูกต้อง — แสดงเดือนปัจจุบันแทน")
        year, month = today.year, today.month
    return year, month, dept


def _departments(db):
    return [d for d in db.execute("SELECT d.id, d.name_th, f.name_th AS factory FROM departments d JOIN factories f ON f.id = d.factory_id "
                                  "ORDER BY f.name_th, d.name_th") if reports.department_in_scope(db, g.user, d["id"]) is not None]


@bp.route("/reports")
@permission_required("report.view")
def index():
    today = timeutil.today()
    return render_template("reports_index.html", names=reports.REPORTS, year=today.year, month=today.month)


@bp.route("/reports/<key>")
@permission_required("report.view")
def view(key):
    if key not in reports.REPORTS:
        abort(404)
    db = get_db()
    year, month, dept = _period()
    rep = reports.build(db, g.user, key, year, month, dept)
    if rep is None:
        abort(404)
    perms = user_permissions(db, g.user)
    return render_template("report_view.html", rep=rep, key=key, year=year, month=month, dept=dept, departments=_departments(db), unscoped=g.user["factory_id"] is None and g.user["department_id"] is None,
                           months=reports.MONTHS_TH, can_export=bool({"report.export"} & perms) or g.user["role"] == "admin",
                           years=list(range(timeutil.today().year, timeutil.today().year - 6, -1)))


@bp.route("/reports/<key>/<fmt>")
@permission_required("report.export")
def export(key, fmt):
    if key not in reports.REPORTS or fmt not in ("xlsx", "pdf"):
        abort(404)
    year, month, dept = _period()
    rep = reports.build(get_db(), g.user, key, year, month, dept)
    if rep is None:
        abort(404)
    try:
        body = report_export.to_xlsx(rep) if fmt == "xlsx" else report_export.to_pdf(rep)
    except report_fonts.FontMissing as exc:
        flash(str(exc))
        return redirect(f"/reports/{key}?year={year}&month={month}" + (f"&department_id={dept}" if dept else ""))
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "application/pdf"
    stamp = f"{year}-{month:02d}" if key == "monthly" else (str(year) if key in ("annual", "incidents") else timeutil.today().isoformat())
    return Response(body, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="report-{key}-{stamp}.{fmt}"', "Cache-Control": "no-store"})
