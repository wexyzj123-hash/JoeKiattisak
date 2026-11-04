"""safety/views/approvals.py — งานรออนุมัติของฉัน, การตัดสินใจอนุมัติ/ไม่อนุมัติ, และหน้าตั้งค่าสายอนุมัติสำหรับผู้ดูแลระบบ"""
from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .. import approvals
from ..db import get_db
from ..hazards import STATUS_TH as HAZARD_STATUS_TH
from ..incidents import INCIDENT_STATUS_TH
from ..perms import permission_required
from ..security import safety_login_required

bp = Blueprint("approvals", __name__)


def is_approver(db, user) -> bool:
    """ผู้ใช้เป็นผู้ดำรงตำแหน่งในสายอนุมัติ (ใช้แสดงเมนู «งานรออนุมัติ»)"""
    if not approvals.enabled(db):
        return False
    code = approvals.position_code(db, user)
    return bool(code) and code in set(approvals.step_codes(db, 1)) | set(approvals.step_codes(db, 2))


@bp.route("/approvals")
@safety_login_required
def queue():
    db = get_db()
    mine = approvals.pending_for(db, g.user)
    for m in mine:
        rec = m["rec"]
        m["title"], m["code"] = rec["title"], rec["code"]
        m["href"] = ("/hazards/" if m["kind"] == "hazard" else "/incidents/") + str(m["ref_id"])
        area = db.execute("SELECT name_th FROM risk_areas WHERE id=?", (rec["risk_area_id"],)).fetchone()
        m["area"] = area[0] if area else ""
        m["reporter"] = (db.execute("SELECT name FROM safety_users WHERE id=?", (rec["reported_by"],)).fetchone() or [""])[0]
        m["level"] = rec["level_name"] if "level_name" in rec.keys() else None
        m["kind_th"] = approvals.KINDS[m["kind"]][1]
    recent = db.execute("SELECT a.*, u.name AS who FROM approvals a LEFT JOIN safety_users u ON u.id = a.approver_id "
                        "WHERE a.approver_id=? ORDER BY a.decided_at DESC LIMIT 20", (g.user["id"],)).fetchall()
    return render_template("approvals.html", items=mine, recent=recent, enabled=approvals.enabled(db), status_th=approvals.STATUS_TH,
                           kinds=approvals.KINDS)


@bp.route("/approvals/<kind>/<int:ref_id>/<int:step>", methods=["POST"])
@safety_login_required
def decide(kind, ref_id, step):
    db = get_db()
    decision = request.form.get("decision", "")
    if decision not in ("approve", "reject"):
        abort(400)
    errors = approvals.decide(db, g.user, kind, ref_id, step, decision == "approve", request.form.get("comment", ""))
    for e in errors:
        flash(e)
    if not errors:
        flash("บันทึกการอนุมัติแล้ว" if decision == "approve" else "บันทึกการไม่อนุมัติแล้ว", "celebrate" if decision == "approve" else "")
    nxt = request.form.get("next", "")
    return redirect(nxt if nxt in ("/approvals",) else "/approvals")


@bp.route("/pending")
@safety_login_required
def pending_fragment():
    """เนื้อหาหน้าต่าง «สรุปงานค้าง» (ส่วนของ HTML ที่ JavaScript โหลดมาใส่ใน dialog)"""
    from .. import pending
    return render_template("_pending.html", d=pending.collect(get_db(), g.user), mascot_ok=True)


@bp.route("/unit")
@safety_login_required
def unit_page():
    from .. import unit
    db = get_db()
    ov = unit.overview(db, g.user)
    notes = db.execute("SELECT id, event, title, body, link, created_at, read_at FROM notifications WHERE user_id=? AND event IN "
                       "('unit_hazard_notice','unit_incident_notice','approval_requested','approval_completed','approval_rejected') "
                       "ORDER BY created_at DESC, id DESC LIMIT 15", (g.user["id"],)).fetchall()
    return render_template("unit.html", ov=ov, notes=notes, enabled=approvals.enabled(db), status_th=approvals.STATUS_TH,
                           hazard_status_th=HAZARD_STATUS_TH, incident_status_th=INCIDENT_STATUS_TH)


@bp.route("/approvals/<kind>/<int:ref_id>/restart", methods=["POST"])
@safety_login_required
def restart(kind, ref_id):
    db = get_db()
    errors = approvals.restart(db, g.user, kind, ref_id)
    for e in errors:
        flash(e)
    if not errors:
        flash("ส่งขออนุมัติใหม่แล้ว")
    if kind not in approvals.KINDS:
        abort(404)
    return redirect(approvals.KINDS[kind][2] + str(ref_id))


@bp.route("/admin/approvals", methods=["GET", "POST"])
@permission_required("users.manage")
def admin():
    db = get_db()
    if request.method == "POST":
        f = request.form
        errors = approvals.save_config(db, f.get("enabled") == "1", f.getlist("step1"), f.getlist("step2"), f.getlist("notify"))
        for e in errors:
            flash(e)
        if not errors:
            flash("บันทึกสายการอนุมัติแล้ว", "celebrate")
        return redirect("/admin/approvals")
    positions = db.execute("SELECT code, name_th FROM positions WHERE active=1 ORDER BY id").fetchall()
    return render_template("approvals_admin.html", enabled=approvals.enabled(db), positions=positions,
                           step1=set(approvals.step_codes(db, 1)), step2=set(approvals.step_codes(db, 2)),
                           notify_set=set(approvals.codes(db, "approval_notify")), matrix=approvals.org_matrix(db))
