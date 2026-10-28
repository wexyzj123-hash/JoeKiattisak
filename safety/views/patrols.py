"""safety/views/patrols.py — Safety Patrol: รายการ/เริ่มรอบ/ตอบแบบตรวจ (เหมาะกับมือถือ)/ส่ง/ข้อค้นพบ/รูปภาพ
รอบที่มองไม่เห็นตอบ 404; รอบที่ส่งแล้วแก้ไม่ได้ (และทริกเกอร์ในฐานข้อมูลกันอีกชั้น)"""
from __future__ import annotations

import math
import os
import re

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file

from .. import capa, patrol, timeutil
from ..db import get_db
from ..hazards import risk_area_options
from ..master import REGISTRY, active_options
from ..perms import permission_any, permission_required, user_permissions
from .hazards import STORED_RE, _int, _precheck

bp = Blueprint("patrols", __name__, url_prefix="/patrols")
ANY_PATROL = ("patrol.perform", "patrol.view")
PER_PAGE = 20
PHOTO_RE = re.compile(r"^photo_(\d+)$")


def _load(round_id: int):
    row = patrol.visible_round(get_db(), g.user, round_id)
    if row is None:
        abort(404)
    return row


def _flash(errors):
    for e in errors:
        flash(e)


@bp.route("")
@permission_any(*ANY_PATROL)
def index():
    db, perms = get_db(), user_permissions(get_db(), g.user)
    page = max(1, _int(request.args.get("page"), 1))
    rows = patrol.rounds_for(db, g.user, limit=PER_PAGE + 1, offset=(page - 1) * PER_PAGE)
    plan_id = _int(request.args.get("plan"))
    return render_template(
        "patrol_list.html", rows=rows[:PER_PAGE], has_next=len(rows) > PER_PAGE, page=page, due=patrol.due_plans(db, g.user), highlight=plan_id,
        can_perform="patrol.perform" in perms, today=timeutil.today(),
        checklists=db.execute("SELECT id, code, name_th FROM checklists WHERE active=1 ORDER BY code").fetchall(), areas=risk_area_options(db, g.user))


@bp.route("/start", methods=["POST"])
@permission_required("patrol.perform")
def start():
    f = request.form
    rid, errors = patrol.start_round(get_db(), g.user, plan_id=_int(f.get("plan_id")) if f.get("plan_id") else None,
                                     checklist_id=f.get("checklist_id"), risk_area_id=f.get("risk_area_id"))
    if rid is None:
        _flash(errors)
        return redirect("/patrols")
    return redirect(f"/patrols/{rid}")


@bp.route("/<int:round_id>")
@permission_any(*ANY_PATROL)
def detail(round_id):
    db, perms = get_db(), user_permissions(get_db(), g.user)
    rnd = _load(round_id)
    answers = patrol.answers_for(db, round_id)
    ids = [a["id"] for a in answers]
    marks = ",".join("?" * len(ids)) or "NULL"
    photos: dict[int, list] = {}
    for p in db.execute(f"SELECT * FROM patrol_attachments WHERE answer_id IN ({marks}) ORDER BY id", ids):
        photos.setdefault(p["answer_id"], []).append(p)
    hazards = {r["patrol_answer_id"]: r for r in db.execute(f"SELECT id, code, patrol_answer_id FROM hazards WHERE patrol_answer_id IN ({marks})", ids)}
    capas = {r["source_id"]: r for r in db.execute(f"SELECT id, code, source_id FROM capas WHERE source_kind='patrol' AND source_id IN ({marks})", ids)}
    editable = rnd["status"] == "draft"
    info = db.execute("SELECT a.name_th AS area_name, c.name_th AS checklist_name, u.name AS inspector_name FROM risk_areas a, checklists c, safety_users u "
                      "WHERE a.id=? AND c.id=? AND u.id=?", (rnd["risk_area_id"], rnd["checklist_id"], rnd["inspector_id"])).fetchone()
    return render_template(
        "patrol_round.html", r=rnd, info=info, answers=answers, photos=photos, hazards=hazards, capas=capas, editable=editable,
        can_find=not editable and "patrol.perform" in perms, hazard_types=active_options(db, REGISTRY["hazard-types"]),
        hazard_sources=active_options(db, REGISTRY["hazard-sources"]), today=timeutil.today().isoformat(),
        owners=db.execute("SELECT id, name FROM safety_users WHERE active=1 ORDER BY name").fetchall())


@bp.route("/<int:round_id>/save", methods=["POST"])
@permission_required("patrol.perform")
def save(round_id):
    _load(round_id)
    db, f = get_db(), request.form
    errors = patrol.save_answers(db, g.user, round_id, f, expected_version=_int(f.get("version")))
    if not errors:
        for key in request.files:
            m = PHOTO_RE.match(key)
            files = [x for x in request.files.getlist(key) if x and x.filename]
            if m and files:
                errors += _precheck(files)
                if errors:
                    break
                for x in files:
                    errors += patrol.add_photo(db, g.user, round_id, int(m.group(1)), x.filename, x.read(), current_app.config["UPLOAD_ROOT"])
    if not errors and f.get("submit"):
        errors = patrol.submit_round(db, g.user, round_id)
        if not errors:
            flash("ส่งผลการตรวจเรียบร้อย — ขอบคุณที่ช่วยดูแลความปลอดภัย", "celebrate")
    _flash(errors)
    return redirect(f"/patrols/{round_id}")


@bp.route("/<int:round_id>/delete", methods=["POST"])
@permission_required("patrol.perform")
def delete(round_id):
    _load(round_id)
    errors = patrol.delete_draft(get_db(), g.user, round_id, current_app.config["UPLOAD_ROOT"])
    _flash(errors)
    return redirect("/patrols" if not errors else f"/patrols/{round_id}")


def _answer_round(answer_id: int) -> int:
    row = get_db().execute("SELECT round_id FROM patrol_answers WHERE id=?", (answer_id,)).fetchone()
    if row is None:
        abort(404)
    return row["round_id"]


def _can_open_hazards(db) -> bool:
    return bool(user_permissions(db, g.user) & {"hazard.report", "hazard.assess", "hazard.assign", "hazard.close", "master.view"})


@bp.route("/answers/<int:answer_id>/hazard", methods=["POST"])
@permission_required("patrol.perform")
def finding_hazard(answer_id):
    db, round_id = get_db(), _answer_round(answer_id)
    hid, errors = patrol.finding_to_hazard(db, g.user, answer_id, _int(request.form.get("hazard_type_id")), _int(request.form.get("source_id")),
                                           current_app.config["UPLOAD_ROOT"])
    if hid is None:
        if errors == patrol.NOT_FOUND:
            abort(404)
        _flash(errors)
        return redirect(f"/patrols/{round_id}")
    return redirect(f"/hazards/{hid}" if _can_open_hazards(db) else f"/patrols/{round_id}")


@bp.route("/answers/<int:answer_id>/capa", methods=["POST"])
@permission_required("patrol.perform")
def finding_capa(answer_id):
    db, round_id = get_db(), _answer_round(answer_id)
    cid, errors = patrol.finding_to_capa(db, g.user, answer_id, request.form)
    if cid is None:
        if errors == patrol.NOT_FOUND:
            abort(404)
        _flash(errors)
        return redirect(f"/patrols/{round_id}")
    return redirect(f"/capas/{cid}")


@bp.route("/<int:round_id>/files/<int:att_id>")
@permission_any(*ANY_PATROL)
def file(round_id, att_id):
    _load(round_id)
    att = get_db().execute("SELECT p.* FROM patrol_attachments p JOIN patrol_answers a ON a.id = p.answer_id WHERE p.id=? AND a.round_id=?",
                           (att_id, round_id)).fetchone()
    if att is None or not STORED_RE.match(att["stored_name"]):
        abort(404)
    path = os.path.join(current_app.config["UPLOAD_ROOT"], f"patrol-{att['answer_id']}", att["stored_name"])
    if not os.path.isfile(path):
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
