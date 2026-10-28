"""safety/views/hazards.py — ลงทะเบียนบันทึกอันตราย: รายการ / แจ้งใหม่ / รายละเอียด / ขั้นตอน / รูปภาพ
ทุกอย่างที่ผู้ใช้มองไม่เห็นตอบ 404 (ไม่เปิดเผยว่ามีรายการอยู่จริง)"""
from __future__ import annotations

import json
import math
import os
import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file

from .. import timeutil, workflow
from ..customfields import fields_for_type
from ..db import get_db
from ..hazards import EVENT_TH, STATUS_TH, can_view_all, hazard_scope_sql, risk_area_options, visible_hazard
from ..images import KINDS, MAX_PER_KIND, ImageError, process_image, save_attachment
from ..master import REGISTRY, active_options
from ..perms import permission_any, permission_required, user_permissions
from ..risk import calc_score, list_levels, scale_size

bp = Blueprint("hazards", __name__, url_prefix="/hazards")
ANY_HAZARD_PERM = ("hazard.report", "hazard.assess", "hazard.assign", "hazard.close", "master.view")
SORTS = {"code": "h.code", "reported_at": "h.reported_at", "score": "h.score", "status": "h.status", "due_date": "h.due_date"}
OPEN = ("reported", "assessed", "assigned")
STORED_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")
PER_PAGE = 20


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _load(hazard_id: int):
    row = visible_hazard(get_db(), g.user, hazard_id)
    if row is None:
        abort(404)
    return row


def _perms() -> set[str]:
    return user_permissions(get_db(), g.user)


def _uploads():
    """ไฟล์ที่เลือกจริง (ข้ามช่องว่างที่เบราว์เซอร์ส่งมาเมื่อไม่ได้เลือกไฟล์)"""
    return [f for f in request.files.getlist("photos") if f and f.filename]


def _precheck(files) -> list[str]:
    errors = []
    for f in files:
        try:
            process_image(f.read())
        except ImageError as exc:
            errors.append(f"{f.filename}: {exc}")
        finally:
            f.stream.seek(0)
    return errors


def _attach(hazard_id: int, kind: str, files) -> list[str]:
    errors = []
    for f in files:
        try:
            save_attachment(get_db(), current_app.config["UPLOAD_ROOT"], hazard_id, kind, f.filename, f.read(), g.user["id"])
        except ImageError as exc:
            errors.append(f"{f.filename}: {exc}")
    return errors


# ---------- รายการ ----------
@bp.route("")
@permission_any(*ANY_HAZARD_PERM)
def index():
    db, perms = get_db(), _perms()
    today = timeutil.today().isoformat()
    conds, args = [], []
    scope_sql, scope_args = hazard_scope_sql(g.user)
    conds.append(scope_sql); args += scope_args
    if not can_view_all(perms):
        conds.append("h.reported_by = ?"); args.append(g.user["id"])
    status = request.args.get("status", "")
    if status in STATUS_TH:
        conds.append("h.status = ?"); args.append(status)
    level = request.args.get("level", "")
    if level == "none":
        conds.append("h.level_name IS NULL")
    elif level:
        conds.append("h.level_name = ?"); args.append(level)
    area = _int(request.args.get("area"))
    if area is not None:
        conds.append("h.risk_area_id = ?"); args.append(area)
    q = request.args.get("q", "").strip()
    if q:
        conds.append("(h.code LIKE ? ESCAPE '\\' OR h.title LIKE ? ESCAPE '\\')"); args += [_like(q)] * 2
    if request.args.get("mine") == "1":
        conds.append("(h.reported_by = ? OR h.owner_id = ?)"); args += [g.user["id"]] * 2
    sort = request.args.get("sort", "reported_at")
    sort = sort if sort in SORTS else "reported_at"
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    where = " AND ".join(conds)
    total = db.execute(f"SELECT COUNT(*) FROM hazards h WHERE {where}", args).fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(_int(request.args.get("page"), 1), 1), pages)
    rows = db.execute(
        f"SELECT h.*, a.name_th AS area_name, u.name AS reporter_name, o.name AS owner_name, "
        f"(h.due_date IS NOT NULL AND h.due_date < ? AND h.status IN ('reported','assessed','assigned')) AS overdue "
        f"FROM hazards h JOIN risk_areas a ON a.id = h.risk_area_id JOIN safety_users u ON u.id = h.reported_by "
        f"LEFT JOIN safety_users o ON o.id = h.owner_id WHERE {where} "
        f"ORDER BY {SORTS[sort]} {direction.upper()}, h.id DESC LIMIT ? OFFSET ?",
        [today] + args + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
    return render_template("hazard_list.html", rows=rows, page=page, pages=pages, total=total, status_th=STATUS_TH,
                           levels=list_levels(db), areas=risk_area_options(db, g.user), f={
                               "status": status, "level": level, "area": area or "", "q": q, "mine": request.args.get("mine", ""),
                               "sort": sort, "dir": direction},
                           can_report="hazard.report" in perms)


# ---------- แจ้งใหม่ ----------
def _form_context(values, errors):
    db = get_db()
    return render_template(
        "hazard_form.html", values=values, errors=errors, areas=risk_area_options(db, g.user),
        types=active_options(db, REGISTRY["hazard-types"]), sources=active_options(db, REGISTRY["hazard-sources"]),
        custom_fields=db.execute("SELECT * FROM custom_fields WHERE active=1 ORDER BY sort, id").fetchall(), max_photos=MAX_PER_KIND)


@bp.route("/new", methods=["GET", "POST"])
@permission_required("hazard.report")
def new():
    if request.method == "GET":
        return _form_context({}, [])
    files = _uploads()
    errors = _precheck(files)
    if len(files) > MAX_PER_KIND:
        errors.append(f"แนบรูปได้ไม่เกิน {MAX_PER_KIND} รูป")
    if errors:
        return _form_context(request.form, errors)
    hazard_id, errors = workflow.report(get_db(), g.user, request.form)
    if errors:
        return _form_context(request.form, errors)
    for message in _attach(hazard_id, "before", files):
        flash(message)
    return redirect(f"/hazards/{hazard_id}")


# ---------- รายละเอียด ----------
def _custom_rows(db, hazard):
    try:
        values = json.loads(hazard["custom"] or "{}")
    except ValueError:
        values = {}
    labels = {f["key"]: f for f in fields_for_type(db, hazard["hazard_type_id"], include_inactive=True)}
    out = []
    for key, value in values.items():
        f = labels.get(key)
        shown = ("ใช่" if value else "ไม่ใช่") if f and f["type"] == "checkbox" else value
        out.append((f["label_th"] if f else key, shown))
    return out


@bp.route("/<int:hazard_id>")
@permission_any(*ANY_HAZARD_PERM)
def detail(hazard_id):
    db, perms = get_db(), _perms()
    h = _load(hazard_id)
    from ..master import breadcrumb
    refs = db.execute("SELECT (SELECT name_th FROM hazard_types WHERE id=?) AS type_name, "
                      "(SELECT name_th FROM hazard_sources WHERE id=?) AS source_name, "
                      "(SELECT name FROM safety_users WHERE id=?) AS reporter, (SELECT name FROM safety_users WHERE id=?) AS owner",
                      (h["hazard_type_id"], h["source_id"], h["reported_by"], h["owner_id"])).fetchone()
    status, is_open = h["status"], h["status"] in OPEN
    mine = h["reported_by"] == g.user["id"]
    can_assess, can_assign, can_close = ("hazard.assess" in perms, "hazard.assign" in perms, "hazard.close" in perms)
    actions = {
        "assess": is_open and status in ("reported", "assessed") and can_assess,
        "assign": status in ("assessed", "assigned") and can_assign,
        "close": status == "assigned" and can_close,
        "reject": is_open and can_assess,
        "photos": is_open and (mine or can_assess or can_assign or can_close),
        "after_photos": status == "assigned" and can_close,
    }
    today = timeutil.today().isoformat()
    events = db.execute("SELECT e.*, u.name AS actor FROM hazard_events e LEFT JOIN safety_users u ON u.id = e.actor_id "
                        "WHERE e.hazard_id=? ORDER BY e.id", (hazard_id,)).fetchall()
    return render_template(
        "hazard_detail.html", h=h, refs=refs, crumb=breadcrumb(db, REGISTRY["risk-areas"], h["risk_area_id"]),
        custom=_custom_rows(db, h), actions=actions, events=events, status_th=STATUS_TH, event_th=EVENT_TH,
        photos={k: db.execute("SELECT * FROM hazard_attachments WHERE hazard_id=? AND kind=? ORDER BY id", (hazard_id, k)).fetchall() for k in KINDS},
        overdue=bool(h["due_date"] and h["due_date"] < today and is_open),
        likelihood_max=scale_size(db, "likelihood"), severity_max=scale_size(db, "severity"),
        scales={k: db.execute("SELECT level, label_th FROM risk_scales WHERE kind=? ORDER BY level", (k,)).fetchall() for k in ("likelihood", "severity")},
        owners=db.execute("SELECT u.id, u.name, COALESCE(r.name_th, u.role) AS role FROM safety_users u "
                          "LEFT JOIN safety_roles r ON r.key = u.role WHERE u.active=1 ORDER BY u.name").fetchall())


def _done(errors: list[str], hazard_id: int):
    for message in errors:
        if message == workflow.NOT_FOUND[0]:
            abort(404)
        flash(message)
    return redirect(f"/hazards/{hazard_id}")


@bp.route("/<int:hazard_id>/assess", methods=["POST"])
@permission_required("hazard.assess")
def assess(hazard_id):
    _load(hazard_id)
    likelihood, severity = _int(request.form.get("likelihood")), _int(request.form.get("severity"))
    if likelihood is None or severity is None:
        return _done(["โอกาสและความรุนแรงต้องเป็นจำนวนเต็มตามเกณฑ์"], hazard_id)
    return _done(workflow.assess(get_db(), g.user, hazard_id, likelihood, severity, request.form.get("note", "")), hazard_id)


@bp.route("/<int:hazard_id>/assign", methods=["POST"])
@permission_required("hazard.assign")
def assign(hazard_id):
    _load(hazard_id)
    return _done(workflow.assign(get_db(), g.user, hazard_id, request.form.get("owner_id"),
                                 request.form.get("due_date", ""), request.form.get("note", "")), hazard_id)


@bp.route("/<int:hazard_id>/close", methods=["POST"])
@permission_required("hazard.close")
def close(hazard_id):
    _load(hazard_id)
    files = _uploads()
    errors = _precheck(files)
    if errors:
        return _done(errors, hazard_id)
    errors = workflow.close(get_db(), g.user, hazard_id, request.form.get("note", ""))
    if not errors:
        errors = _attach(hazard_id, "after", files)
    return _done(errors, hazard_id)


@bp.route("/<int:hazard_id>/reject", methods=["POST"])
@permission_required("hazard.assess")
def reject(hazard_id):
    _load(hazard_id)
    return _done(workflow.reject(get_db(), g.user, hazard_id, request.form.get("reason", "")), hazard_id)


@bp.route("/<int:hazard_id>/photos", methods=["POST"])
@permission_any(*ANY_HAZARD_PERM)
def photos(hazard_id):
    h, perms = _load(hazard_id), _perms()
    kind = request.form.get("kind", "before")
    mine = h["reported_by"] == g.user["id"]
    if h["status"] not in OPEN:
        return _done(["รายการนี้ปิดหรือถูกปฏิเสธแล้ว แนบรูปเพิ่มไม่ได้"], hazard_id)
    if kind == "before":
        allowed = mine or bool(perms & {"hazard.assess", "hazard.assign", "hazard.close"})
    elif kind == "after":
        allowed = h["status"] == "assigned" and "hazard.close" in perms
    else:
        allowed = False
    if not allowed:
        abort(403)
    return _done(_attach(hazard_id, kind, _uploads()), hazard_id)


@bp.route("/<int:hazard_id>/files/<int:att_id>")
@permission_any(*ANY_HAZARD_PERM)
def file(hazard_id, att_id):
    _load(hazard_id)
    att = get_db().execute("SELECT * FROM hazard_attachments WHERE id=? AND hazard_id=?", (att_id, hazard_id)).fetchone()
    if att is None or not STORED_RE.match(att["stored_name"]):
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_ROOT"], str(hazard_id), att["stored_name"])
    if not os.path.isfile(path):
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
