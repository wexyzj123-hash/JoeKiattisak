"""safety/views/capas.py — CAPA: รายการ / สร้าง / รายละเอียด / ขั้นตอน และตั้งค่าประตูปิดรายการ
CAPA ที่มองไม่เห็นตอบ 404; ผู้ยืนยันต้องไม่เคยเป็นเจ้าของและไม่ใช่ผู้กดว่าเสร็จ (ดู safety/capa.py)"""
from __future__ import annotations

import math

from flask import Blueprint, abort, flash, g, redirect, render_template, request

from .. import capa, timeutil
from ..db import get_db
from ..hazards import risk_area_options, user_in_scope, visible_hazard
from ..incidents import visible_incident
from ..patrol import visible_round
from ..perms import permission_any, permission_required, user_permissions
from ..security import safety_login_required
from .hazards import _int

bp = Blueprint("capas", __name__)
ANY_CAPA = ("hazard.report", "incident.report", "patrol.perform", "capa.create", "capa.verify", "hazard.assign", "incident.investigate")
PER_PAGE = 20


def _load(capa_id: int):
    row = capa.visible_capa(get_db(), g.user, capa_id)
    if row is None:
        abort(404)
    return row


def _flash(errors):
    for e in errors:
        flash(e)


@bp.route("/capas")
@permission_any(*ANY_CAPA)
def index():
    db = get_db()
    status, q = request.args.get("status", ""), request.args.get("q", "").strip()[:100]
    mine, overdue = request.args.get("mine") == "1", request.args.get("overdue") == "1"
    page_in = max(1, _int(request.args.get("page"), 1))
    _, total = capa.list_capas(db, g.user, status=status, mine=mine, overdue=overdue, q=q, limit=1)
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page_in, pages)
    rows, _ = capa.list_capas(db, g.user, status=status, mine=mine, overdue=overdue, q=q, limit=PER_PAGE, offset=(page - 1) * PER_PAGE)
    return render_template("capa_list.html", rows=rows, page=page, pages=pages, total=total, status_th=capa.STATUS_TH, kind_th=capa.KIND_TH,
                           today=timeutil.today().isoformat(), can_create="capa.create" in user_permissions(db, g.user),
                           f={"status": status, "q": q, "mine": "1" if mine else "", "overdue": "1" if overdue else ""})


def _source_from(raw: str):
    """'hazard:12' → (('hazard', 12), หัวข้อต้นทาง) หรือ 404 ถ้าไม่พบ/มองไม่เห็น"""
    raw = (raw or "").strip()
    if not raw:
        return None, ""
    kind, _, sid = raw.partition(":")
    sid = _int(sid)
    db = get_db()
    row = visible_hazard(db, g.user, sid) if kind == "hazard" and sid else visible_incident(db, g.user, sid) if kind == "incident" and sid else None
    if row is None:
        abort(404)
    return (kind, sid), f"{row['code']} · {row['title']}"


def _owner_choices(db, user, area_id: int | None = None) -> list:
    """เจ้าของงานที่เลือกได้: ถ้าทราบพื้นที่ = ผู้ที่ขอบเขตครอบคลุมพื้นที่นั้น; ไม่ทราบ = ผู้ที่ขอบเขตทับซ้อนกับผู้สร้าง (ไม่แสดงรายชื่อทั้งบริษัท)"""
    rows = db.execute("SELECT id, name, factory_id, department_id FROM safety_users WHERE active=1 ORDER BY name").fetchall()
    if area_id is not None:
        return [r for r in rows if user_in_scope(db, r, area_id)]
    return [r for r in rows if (user["factory_id"] is None or r["factory_id"] in (None, user["factory_id"]))
            and (user["department_id"] is None or r["department_id"] in (None, user["department_id"]))]


def _form(values, errors, source_raw, source_title):
    db = get_db()
    return render_template("capa_form.html", values=values, errors=errors, source=source_raw, source_title=source_title, types=capa.TYPE_TH,
                           areas=risk_area_options(db, g.user),
                           owners=_owner_choices(db, g.user))


@bp.route("/capas/new", methods=["GET", "POST"])
@permission_required("capa.create")
def new():
    raw = request.values.get("source", "")
    source, title = _source_from(raw)
    if request.method == "GET":
        return _form({}, [], raw, title)
    cid, errors = capa.create(get_db(), g.user, request.form, source)
    if errors:
        return _form(request.form, errors, raw, title)
    return redirect(f"/capas/{cid}")


@bp.route("/capas/<int:capa_id>")
@safety_login_required
def detail(capa_id):
    db, perms = get_db(), user_permissions(get_db(), g.user)
    c = _load(capa_id)
    status = c["status"]
    can_create = "capa.create" in perms or g.user["role"] == "admin"
    owners_ever = capa.owners_ever(db, c)
    verifier = "capa.verify" in perms
    blocked = ""
    if verifier and status == "done":
        if g.user["id"] in owners_ever:
            blocked = "คุณเคยเป็นเจ้าของงานนี้ จึงยืนยันผลเองไม่ได้ — ต้องให้ผู้อื่นที่มีสิทธิ์ยืนยัน"
        elif g.user["id"] in capa.done_ever(db, c):
            blocked = "คุณเป็นผู้ที่กดว่าเสร็จ จึงยืนยันผลเองไม่ได้ — ต้องให้ผู้อื่นที่มีสิทธิ์ยืนยัน (ผู้ที่กดว่าเสร็จยืนยันเองไม่ได้)"
    today = timeutil.today().isoformat()
    actions = {
        "start": status == "open" and g.user["id"] == c["owner_id"],
        "complete": status in capa.OPEN_STATES and (g.user["id"] == c["owner_id"] or can_create),
        "verify": verifier and status == "done" and not blocked,
        "blocked": blocked,
        "cancel": status in capa.OPEN_STATES and (g.user["id"] == c["created_by"] or can_create),
        "reassign": status in capa.OPEN_STATES and can_create,
        "effectiveness": (verifier and status == "verified" and bool(c["effectiveness_due"]) and not c["effectiveness_result"]
                          and g.user["id"] not in owners_ever and c["effectiveness_due"] <= today),
    }
    names = db.execute("SELECT (SELECT name FROM safety_users WHERE id=?) AS owner, (SELECT name FROM safety_users WHERE id=?) AS creator, "
                       "(SELECT name FROM safety_users WHERE id=?) AS done_by, (SELECT name FROM safety_users WHERE id=?) AS verified_by, "
                       "(SELECT name_th FROM risk_areas WHERE id=?) AS area", (c["owner_id"], c["created_by"], c["done_by"], c["verified_by"], c["risk_area_id"])).fetchone()
    source = None                                                     # แสดงต้นทางเฉพาะที่ผู้ดูมีสิทธิ์เห็นรายการนั้นจริง
    if c["source_kind"] == "hazard":
        source = visible_hazard(db, g.user, c["source_id"])
    elif c["source_kind"] == "incident":
        source = visible_incident(db, g.user, c["source_id"])
    elif c["source_kind"] == "patrol":
        ans = db.execute("SELECT r.id, r.code, a.text_th AS title FROM patrol_answers a JOIN patrol_rounds r ON r.id = a.round_id WHERE a.id=?", (c["source_id"],)).fetchone()
        source = ans if ans is not None and visible_round(db, g.user, ans["id"]) is not None else None
    events = db.execute("SELECT e.*, u.name AS actor FROM capa_events e LEFT JOIN safety_users u ON u.id = e.actor_id WHERE e.capa_id=? ORDER BY e.id", (capa_id,)).fetchall()
    return render_template("capa_detail.html", c=c, names=names, source=source, actions=actions, events=events, status_th=capa.STATUS_TH, kind_th=capa.KIND_TH,
                           type_th=capa.TYPE_TH, event_th=capa.EVENT_TH, overdue=bool(status in capa.OPEN_STATES and c["due_date"] < today),
                           owners=_owner_choices(db, g.user, c["risk_area_id"]))


def _act(capa_id, fn, *args, perm_ok=True):
    _load(capa_id)
    _flash(fn(get_db(), g.user, capa_id, *args))
    return redirect(f"/capas/{capa_id}")


@bp.route("/capas/<int:capa_id>/start", methods=["POST"])
@safety_login_required
def start(capa_id):
    return _act(capa_id, capa.start)


@bp.route("/capas/<int:capa_id>/complete", methods=["POST"])
@safety_login_required
def complete(capa_id):
    f = request.form
    return _act(capa_id, lambda db, u, cid: capa.complete(db, u, cid, f.get("note", ""), expected_version=_int(f.get("version"))))


@bp.route("/capas/<int:capa_id>/verify", methods=["POST"])
@permission_required("capa.verify")
def verify(capa_id):
    f = request.form
    return _act(capa_id, lambda db, u, cid: capa.verify(db, u, cid, f.get("note", ""), f.get("effectiveness_due", ""), expected_version=_int(f.get("version"))))


@bp.route("/capas/<int:capa_id>/reopen", methods=["POST"])
@permission_required("capa.verify")
def reopen(capa_id):
    f = request.form
    return _act(capa_id, lambda db, u, cid: capa.reopen(db, u, cid, f.get("reason", ""), expected_version=_int(f.get("version"))))


@bp.route("/capas/<int:capa_id>/cancel", methods=["POST"])
@safety_login_required
def cancel(capa_id):
    return _act(capa_id, lambda db, u, cid: capa.cancel(db, u, cid, request.form.get("reason", "")))


@bp.route("/capas/<int:capa_id>/reassign", methods=["POST"])
@permission_required("capa.create")
def reassign(capa_id):
    f = request.form
    return _act(capa_id, lambda db, u, cid: capa.reassign(db, u, cid, f.get("owner_id"), f.get("due_date", "")))


@bp.route("/capas/<int:capa_id>/effectiveness", methods=["POST"])
@permission_required("capa.verify")
def effectiveness(capa_id):
    f = request.form
    return _act(capa_id, lambda db, u, cid: capa.record_effectiveness(db, u, cid, f.get("result", ""), f.get("note", "")))


@bp.route("/admin/capa-settings", methods=["GET", "POST"])
@permission_required("criteria.manage")
def settings():
    db = get_db()
    if request.method == "POST":
        value = "1" if request.form.get("require_capa_verified") == "1" else "0"
        db.execute("INSERT INTO app_settings(key, value) VALUES('require_capa_verified', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
        db.commit()
        flash("บันทึกการตั้งค่าแล้ว")
        return redirect("/admin/capa-settings")
    return render_template("capa_settings.html", enabled=capa.gate_enabled(db))
