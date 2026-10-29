"""safety/views/incidents.py — อุบัติเหตุ/Near Miss: รายการ / แจ้งใหม่ / รายละเอียด / ขั้นตอน / รูปภาพ
ทุกอย่างที่ผู้ใช้มองไม่เห็นตอบ 404; ชื่อผู้เกี่ยวข้องแสดงเฉพาะผู้มีสิทธิ์ (ดู safety/incidents.py)"""
from __future__ import annotations

import math
import os

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file

from .. import capa, charts, incident_flow, incident_stats, incidents, timeutil
from ..db import get_db
from ..hazards import risk_area_options, user_in_scope
from ..images import MAX_PER_KIND, ImageError, save_attachment
from ..incidents import (INCIDENT_EVENT_TH, INCIDENT_KIND_TH, INCIDENT_STATUS_TH, OPEN_STATUSES, PEOPLE_ROLES, can_view_all, people_count,
                         people_for, scope_sql, visible_incident)
from ..master import breadcrumb, REGISTRY
from ..perms import permission_any, permission_required, user_permissions
from ..risk import scale_size
from .hazards import STORED_RE, _int, _like, _precheck, _uploads

bp = Blueprint("incidents", __name__, url_prefix="/incidents")
ANY_INCIDENT_PERM = ("incident.report", "incident.investigate", "incident.close")
PER_PAGE = 20
MAX_PEOPLE_SLOTS = incidents.MAX_PEOPLE


def _load(incident_id: int):
    row = visible_incident(get_db(), g.user, incident_id)
    if row is None:
        abort(404)
    return row


def _perms() -> set[str]:
    return user_permissions(get_db(), g.user)


def _attach(incident_id: int, kind: str, files) -> list[str]:
    errors = []
    for f in files:
        try:
            save_attachment(get_db(), current_app.config["UPLOAD_ROOT"], incident_id, kind, f.filename, f.read(), g.user["id"], scope="incident")
        except ImageError as exc:
            errors.append(f"{f.filename}: {exc}")
    return errors


def _done(errors: list[str], incident_id: int):
    for message in errors:
        if message == incidents.NOT_FOUND[0]:
            abort(404)
        flash(message)
    return redirect(f"/incidents/{incident_id}")


# ---------- รายการ ----------
@bp.route("")
@permission_any(*ANY_INCIDENT_PERM)
def index():
    db, perms = get_db(), _perms()
    sql, args = scope_sql(g.user)
    conds, args = [sql], list(args)
    if not can_view_all(perms):
        conds.append("(i.reported_by = ? OR i.owner_id = ?)"); args += [g.user["id"]] * 2
    kind, status = request.args.get("kind", ""), request.args.get("status", "")
    if kind in INCIDENT_KIND_TH:
        conds.append("i.kind = ?"); args.append(kind)
    if status in INCIDENT_STATUS_TH:
        conds.append("i.status = ?"); args.append(status)
    q = request.args.get("q", "").strip()[:100]
    if q:
        conds.append("(i.code LIKE ? ESCAPE '\\' OR i.title LIKE ? ESCAPE '\\')"); args += [_like(q)] * 2
    if request.args.get("mine") == "1":
        conds.append("(i.reported_by = ? OR i.owner_id = ?)"); args += [g.user["id"]] * 2
    where = " AND ".join(conds)
    total = db.execute(f"SELECT COUNT(*) FROM incidents i WHERE {where}", args).fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(_int(request.args.get("page"), 1), 1), pages)
    rows = db.execute(
        f"SELECT i.*, a.name_th AS area_name, s.name_th AS severity_name, "
        f"(SELECT COUNT(*) FROM incident_people p WHERE p.incident_id = i.id) AS people_n "
        f"FROM incidents i JOIN risk_areas a ON a.id = i.risk_area_id LEFT JOIN injury_severities s ON s.id = i.injury_severity_id "
        f"WHERE {where} ORDER BY i.occurred_at DESC, i.id DESC LIMIT ? OFFSET ?", args + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    return render_template("incident_list.html", rows=rows, page=page, pages=pages, total=total, status_th=INCIDENT_STATUS_TH,
                           kind_th=INCIDENT_KIND_TH, can_report="incident.report" in perms,
                           f={"kind": kind, "status": status, "q": q, "mine": request.args.get("mine", "")})


# ---------- สถิติ ----------
@bp.route("/stats")
@permission_required("incident.investigate")
def stats():
    db, user = get_db(), g.user
    days, last = incident_stats.days_without_lost_time(db, user)
    monthly = incident_stats.monthly_incidents(db, user)
    sev = incident_stats.by_severity(db, user)
    donut_svg, legend = charts.donut(sev, label="อุบัติเหตุแยกตามระดับการบาดเจ็บ", center="อุบัติเหตุ")
    return render_template(
        "incident_stats.html", days=days, last=last, counts=incident_stats.counts_by_kind_status(db, user), monthly=monthly,
        trend_svg=charts.trend(monthly, labels=("อุบัติเหตุ", "Near Miss"), colors=("#ea580c", "#ca8a04"), title="อุบัติเหตุและ Near Miss 12 เดือน"),
        donut=donut_svg, legend=legend, areas=incident_stats.by_area(db, user), status_th=INCIDENT_STATUS_TH, kind_th=INCIDENT_KIND_TH)


# ---------- แจ้งใหม่ ----------
def _form_context(values, errors):
    db = get_db()
    return render_template(
        "incident_form.html", values=values, errors=errors, areas=risk_area_options(db, g.user), max_photos=MAX_PER_KIND,
        severities=db.execute("SELECT * FROM injury_severities WHERE active=1 ORDER BY rank, id").fetchall(),
        scales={k: db.execute("SELECT level, label_th FROM risk_scales WHERE kind=? ORDER BY level", (k,)).fetchall() for k in ("likelihood", "severity")},
        people_roles=PEOPLE_ROLES, kind_th=INCIDENT_KIND_TH, slots=MAX_PEOPLE_SLOTS, visible_slots=5)


@bp.route("/new", methods=["GET", "POST"])
@permission_required("incident.report")
def new():
    if request.method == "GET":
        return _form_context({}, [])
    files = _uploads()
    errors = _precheck(files)
    if len(files) > MAX_PER_KIND:
        errors.append(f"แนบรูปได้ไม่เกิน {MAX_PER_KIND} รูป")
    if errors:
        return _form_context(request.form, errors)
    incident_id, errors = incidents.report(get_db(), g.user, request.form)
    if errors:
        return _form_context(request.form, errors)
    for message in _attach(incident_id, "before", files):
        flash(message)
    return redirect(f"/incidents/{incident_id}")


# ---------- รายละเอียด ----------
@bp.route("/<int:incident_id>")
@permission_any(*ANY_INCIDENT_PERM)
def detail(incident_id):
    db, perms = get_db(), _perms()
    inc = _load(incident_id)
    refs = db.execute(
        "SELECT (SELECT name FROM safety_users WHERE id=?) AS reporter, (SELECT name FROM safety_users WHERE id=?) AS owner, "
        "(SELECT name_th FROM injury_severities WHERE id=?) AS severity_name, (SELECT name_th FROM incident_causes WHERE id=?) AS cause_name, "
        "(SELECT code FROM hazards WHERE id=?) AS hazard_code",
        (inc["reported_by"], inc["owner_id"], inc["injury_severity_id"], inc["cause_id"], inc["related_hazard_id"])).fetchone()
    status, is_open = inc["status"], inc["status"] in OPEN_STATUSES
    mine = g.user["id"] in (inc["reported_by"], inc["owner_id"])
    inv, closer = "incident.investigate" in perms, "incident.close" in perms
    was_investigator = g.user["id"] in incident_flow.investigators_ever(db, inc)
    actions = {
        "assign": inv and status in ("reported", "investigating"),
        "investigate": inv and status in ("investigating", "actions_set") and (g.user["id"] == inc["owner_id"] or g.user["role"] == "admin"),
        "close": closer and status == "actions_set" and not was_investigator,
        "close_blocked": closer and status == "actions_set" and was_investigator,
        "reject": inv and is_open,
        "reclassify": inv and inc["kind"] == "accident" and status != "rejected",
        "people": inv and is_open,
        "photos": is_open and (mine or inv or closer),
        "after_photos": status == "actions_set" and (inv or closer),
    }
    people = people_for(db, g.user, inc)
    owners = []
    if actions["assign"]:
        owners = [o for o in db.execute("SELECT u.id, u.name, u.factory_id, u.department_id, u.role, COALESCE(r.name_th, u.role) AS role_name "
                                        "FROM safety_users u LEFT JOIN safety_roles r ON r.key = u.role WHERE u.active=1 ORDER BY u.name")
                  if "incident.investigate" in user_permissions(db, o) and user_in_scope(db, o, inc["risk_area_id"])]
    events = db.execute("SELECT e.*, u.name AS actor FROM incident_events e LEFT JOIN safety_users u ON u.id = e.actor_id "
                        "WHERE e.incident_id=? ORDER BY e.id", (incident_id,)).fetchall()
    sev = db.execute("SELECT counts_lost_time FROM injury_severities WHERE id=?", (inc["injury_severity_id"],)).fetchone()
    lost_time = inc["kind"] == "accident" and bool(sev and sev[0])
    slots = min(MAX_PEOPLE_SLOTS, (len(people) if people else 0) + 3)
    return render_template(
        "incident_detail.html", capas=capa.for_source(db, g.user, "incident", incident_id), can_capa="capa.create" in perms, i=inc, refs=refs, crumb=breadcrumb(db, REGISTRY["risk-areas"], inc["risk_area_id"]), actions=actions,
        people=people, people_n=people_count(db, incident_id), people_roles=PEOPLE_ROLES, slots=slots, owners=owners, events=events,
        status_th=INCIDENT_STATUS_TH, kind_th=INCIDENT_KIND_TH, event_th=INCIDENT_EVENT_TH,
        severities=db.execute("SELECT * FROM injury_severities WHERE active=1 ORDER BY rank, id").fetchall(),
        causes=db.execute("SELECT id, name_th FROM incident_causes WHERE active=1 ORDER BY code").fetchall(),
        lost_time=lost_time,
        photos={k: db.execute("SELECT * FROM incident_attachments WHERE incident_id=? AND kind=? ORDER BY id", (incident_id, k)).fetchall()
                for k in ("before", "after")},
        overdue=bool(inc["due_date"] and inc["due_date"] < timeutil.today().isoformat() and is_open))


@bp.route("/<int:incident_id>/assign", methods=["POST"])
@permission_required("incident.investigate")
def assign(incident_id):
    _load(incident_id)
    return _done(incident_flow.assign_investigator(get_db(), g.user, incident_id, request.form.get("owner_id"),
                                                   request.form.get("due_date", ""), request.form.get("note", "")), incident_id)


@bp.route("/<int:incident_id>/investigate", methods=["POST"])
@permission_required("incident.investigate")
def investigate(incident_id):
    _load(incident_id)
    f = request.form
    return _done(incident_flow.record_investigation(get_db(), g.user, incident_id, f.get("cause_id"), f.get("root_cause", ""),
                                                    f.get("corrective_action", ""), f.get("lost_days"),
                                                    expected_version=_int(f.get("version"))), incident_id)


@bp.route("/<int:incident_id>/close", methods=["POST"])
@permission_required("incident.close")
def close(incident_id):
    _load(incident_id)
    files = _uploads()
    errors = _precheck(files)
    if errors:
        return _done(errors, incident_id)
    errors = incident_flow.close(get_db(), g.user, incident_id, request.form.get("note", ""))
    if not errors:
        flash("ปิดรายการเรียบร้อย — ขอบคุณที่ช่วยให้โรงงานปลอดภัยขึ้น", "celebrate")
        errors = _attach(incident_id, "after", files)
    return _done(errors, incident_id)


@bp.route("/<int:incident_id>/reclassify", methods=["POST"])
@permission_required("incident.investigate")
def reclassify(incident_id):
    _load(incident_id)
    f = request.form
    return _done(incident_flow.reclassify(get_db(), g.user, incident_id, f.get("injury_severity_id"), f.get("reason", ""), f.get("lost_days"),
                                          expected_version=_int(f.get("version"))), incident_id)


@bp.route("/<int:incident_id>/reject", methods=["POST"])
@permission_required("incident.investigate")
def reject(incident_id):
    _load(incident_id)
    return _done(incident_flow.reject(get_db(), g.user, incident_id, request.form.get("reason", "")), incident_id)


@bp.route("/<int:incident_id>/people", methods=["POST"])
@permission_required("incident.investigate")
def people(incident_id):
    _load(incident_id)
    return _done(incident_flow.update_people(get_db(), g.user, incident_id, request.form), incident_id)


@bp.route("/<int:incident_id>/photos", methods=["POST"])
@permission_any(*ANY_INCIDENT_PERM)
def photos(incident_id):
    inc, perms = _load(incident_id), _perms()
    kind = request.form.get("kind", "before")
    mine = g.user["id"] in (inc["reported_by"], inc["owner_id"])
    if inc["status"] not in OPEN_STATUSES:
        return _done(["รายการนี้ปิดหรือถูกปฏิเสธแล้ว แนบรูปเพิ่มไม่ได้"], incident_id)
    if kind == "before":
        allowed = mine or bool(perms & {"incident.investigate", "incident.close"})
    elif kind == "after":
        allowed = inc["status"] == "actions_set" and bool(perms & {"incident.investigate", "incident.close"})
    else:
        allowed = False
    if not allowed:
        abort(403)
    return _done(_attach(incident_id, kind, _uploads()), incident_id)


@bp.route("/<int:incident_id>/files/<int:att_id>")
@permission_any(*ANY_INCIDENT_PERM)
def file(incident_id, att_id):
    _load(incident_id)
    att = get_db().execute("SELECT * FROM incident_attachments WHERE id=? AND incident_id=?", (att_id, incident_id)).fetchone()
    if att is None or not STORED_RE.match(att["stored_name"]):
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_ROOT"], f"incident-{incident_id}", att["stored_name"])
    if not os.path.isfile(path):
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
