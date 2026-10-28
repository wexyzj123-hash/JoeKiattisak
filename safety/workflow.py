"""safety/workflow.py — ขั้นตอนของรายการอันตราย: แจ้ง → ประเมิน → มอบหมาย → ปิด / ปฏิเสธ
ทุกฟังก์ชันตรวจสิทธิ์ → การมองเห็น → สถานะ → ข้อมูล แล้วเปลี่ยนสถานะพร้อมบันทึกเหตุการณ์ใน transaction เดียว
การเปลี่ยนสถานะใช้ UPDATE ... WHERE status = <ที่อ่านมา> เมื่อมีคนตัดหน้าจะไม่เขียนอะไร (optimistic lock)"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Mapping

from . import notify, timeutil
from .customfields import fields_for_type, validate_custom
from .hazards import add_event, next_code, risk_area_options, user_in_scope, visible_hazard
from .perms import user_permissions
from .risk import calc_score, level_for_score

TITLE_MAX, DESC_MAX, NOTE_MAX = 200, 2000, 1000
OPEN = ("reported", "assessed", "assigned")
NOT_FOUND = ["ไม่พบรายการ"]
LOST_RACE = ["รายการถูกเปลี่ยนสถานะโดยผู้อื่นแล้ว กรุณารีเฟรชหน้า"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _need(db, user, perm: str, what: str) -> list[str]:
    return [] if perm in user_permissions(db, user) else [f"คุณไม่มีสิทธิ์{what}"]


def _transition(db, hazard, sets: dict, kind: str, user, to_status: str, note: str = "", data: dict | None = None,
                notify_event: str | None = None) -> list[str]:
    cols = ", ".join(f"{c}=?" for c in sets)
    # ตรวจทั้งสถานะและ version — กันคนตัดหน้าแม้สถานะเดิม (ประเมินซ้ำ/โอนงาน) แล้วบันทึกเหตุการณ์ผิดจากความจริง
    cur = db.execute(f"UPDATE hazards SET {cols}, status=?, version=version+1, updated_at=datetime('now','localtime') "
                     "WHERE id=? AND status=? AND version=?",
                     [*sets.values(), to_status, hazard["id"], hazard["status"], hazard["version"]])
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    add_event(db, hazard["id"], user["id"], kind, hazard["status"], to_status, note, data)
    if notify_event:
        notify.emit(db, notify_event, hazard["id"], user["id"])       # แถวแจ้งเตือนอยู่ใน transaction เดียวกับการเปลี่ยนสถานะ
    db.commit()
    return []


def report(db, user, form: Mapping) -> tuple[int | None, list[str]]:
    errors = _need(db, user, "hazard.report", "แจ้งอันตราย")
    if errors:
        return None, errors
    title, desc = (form.get("title") or "").strip(), (form.get("description") or "").strip()
    if not title:
        errors.append("กรุณากรอกหัวข้ออันตราย")
    elif len(title) > TITLE_MAX:
        errors.append(f"หัวข้อยาวเกิน {TITLE_MAX} ตัวอักษร")
    if len(desc) > DESC_MAX:
        errors.append(f"รายละเอียดยาวเกิน {DESC_MAX} ตัวอักษร")
    area = _int(form.get("risk_area_id"))
    if area is None or area not in {o["id"] for o in risk_area_options(db, user)}:
        errors.append("กรุณาเลือกพื้นที่เสี่ยงที่ใช้งานอยู่และอยู่ในขอบเขตของคุณ")
    type_id, source_id = _int(form.get("hazard_type_id")), _int(form.get("source_id"))
    if type_id is None or not db.execute("SELECT 1 FROM hazard_types WHERE id=? AND active=1", (type_id,)).fetchone():
        errors.append("กรุณาเลือกประเภทอันตราย")
        type_id = None
    if source_id is None or not db.execute("SELECT 1 FROM hazard_sources WHERE id=? AND active=1", (source_id,)).fetchone():
        errors.append("กรุณาเลือกแหล่งที่มา")
    custom: dict = {}
    if type_id is not None:
        custom, cerrors = validate_custom(fields_for_type(db, type_id), form)
        errors += cerrors
    if errors:
        return None, errors
    for attempt in (1, 2):
        try:
            cur = db.execute("INSERT INTO hazards(code,title,description,risk_area_id,hazard_type_id,source_id,reported_by,"
                             "status,custom) VALUES(?,?,?,?,?,?,?,'reported',?)",
                             (next_code(db), title, desc, area, type_id, source_id, user["id"],
                              json.dumps(custom, ensure_ascii=False)))
            break
        except sqlite3.IntegrityError:                  # รหัสชนกันตอนสร้างพร้อมกัน — คำนวณใหม่แล้วลองอีกครั้ง
            db.rollback()
            if attempt == 2:
                return None, ["สร้างรหัสรายการไม่สำเร็จ กรุณาลองอีกครั้ง"]
    add_event(db, cur.lastrowid, user["id"], "reported", None, "reported")
    notify.emit(db, "hazard_reported", cur.lastrowid, user["id"])
    db.commit()
    return cur.lastrowid, []


def _open_for(db, user, perm: str, what: str, hazard_id, allowed: tuple[str, ...]):
    """ตรวจสิทธิ์ + การมองเห็น + สถานะ; คืน (hazard, errors)"""
    errors = _need(db, user, perm, what)
    if errors:
        return None, errors
    hazard = visible_hazard(db, user, hazard_id)
    if hazard is None:
        return None, NOT_FOUND
    if hazard["status"] not in allowed:
        return None, ["สถานะของรายการนี้ไม่อนุญาตให้ทำรายการดังกล่าว"]
    return hazard, []


def assess(db, user, hazard_id: int, likelihood: int, severity: int, note: str = "") -> list[str]:
    hazard, errors = _open_for(db, user, "hazard.assess", "ประเมินความเสี่ยง", hazard_id, ("reported", "assessed"))
    if errors:
        return errors
    try:
        score = calc_score(db, likelihood, severity)
    except ValueError as exc:
        return [str(exc)]
    level = level_for_score(db, score)
    if level is None:
        return ["คะแนนนี้ไม่มีระดับความเสี่ยงรองรับ กรุณาแจ้งผู้ดูแลระบบตรวจเกณฑ์ความเสี่ยง"]
    old = {"likelihood": hazard["likelihood"], "severity": hazard["severity"], "score": hazard["score"],
           "level": hazard["level_name"]}
    new = {"likelihood": likelihood, "severity": severity, "score": score, "level": level["name_th"]}
    return _transition(db, hazard, {
        "likelihood": likelihood, "severity": severity, "score": score, "risk_level_id": level["id"],
        "level_name": level["name_th"], "level_color": level["color"],
        "level_requires_approval": level["requires_approval"]}, "assessed", user, "assessed",
        (note or "").strip()[:NOTE_MAX], {"old": old, "new": new}, "hazard_assessed")


def assign(db, user, hazard_id: int, owner_id, due_date: str, note: str = "") -> list[str]:
    hazard, errors = _open_for(db, user, "hazard.assign", "มอบหมายผู้รับผิดชอบ", hazard_id, ("assessed", "assigned"))
    if errors:
        return errors
    owner = _int(owner_id)
    owner_row = db.execute("SELECT * FROM safety_users WHERE id=? AND active=1", (owner,)).fetchone() if owner is not None else None
    if owner_row is None:
        return ["กรุณาเลือกผู้รับผิดชอบที่ใช้งานอยู่"]
    if not user_in_scope(db, owner_row, hazard["risk_area_id"]):
        return ["ผู้รับผิดชอบที่เลือกอยู่นอกขอบเขตของรายการนี้"]
    today = timeutil.today()
    raw = (due_date or "").strip()
    if raw:
        try:
            if not DATE_RE.match(raw):
                raise ValueError
            due = date.fromisoformat(raw)
        except ValueError:
            return ["วันครบกำหนดต้องเป็นรูปแบบ ปปปป-ดด-วว ที่มีอยู่จริง"]
        if due < today:
            return ["วันครบกำหนดต้องไม่เป็นวันในอดีต"]
        if due > today + timedelta(days=365):
            return ["วันครบกำหนดต้องไม่เกิน 1 ปีนับจากวันนี้"]
    else:
        level = level_for_score(db, hazard["score"])
        due = today + timedelta(days=level["sla_days"] if level else 0)
    return _transition(db, hazard, {"owner_id": owner, "due_date": due.isoformat()}, "assigned", user, "assigned",
                       (note or "").strip()[:NOTE_MAX], {"old_owner": hazard["owner_id"], "owner": owner,
                                                         "due_date": due.isoformat()}, "hazard_assigned")


def _owners_ever(db, hazard) -> set[int]:
    """ผู้รับผิดชอบปัจจุบันและทุกคนที่เคยรับผิดชอบ (จากประวัติ) — โอนงานออกแล้วก็ยังปิดเองไม่ได้"""
    owners = {hazard["owner_id"]} - {None}
    for row in db.execute("SELECT data FROM hazard_events WHERE hazard_id=? AND kind='assigned'", (hazard["id"],)):
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            continue
        owners |= {data.get("owner"), data.get("old_owner")} - {None}
    return owners


def close(db, user, hazard_id: int, note: str) -> list[str]:
    hazard, errors = _open_for(db, user, "hazard.close", "ปิดรายการอันตราย", hazard_id, ("assigned",))
    if errors:
        return errors
    note = (note or "").strip()
    if not note:
        return ["กรุณาระบุผลการแก้ไข"]
    if len(note) > NOTE_MAX:
        return [f"ผลการแก้ไขยาวเกิน {NOTE_MAX} ตัวอักษร"]
    if hazard["level_requires_approval"] and user["id"] in _owners_ever(db, hazard):
        return [f"รายการระดับ {hazard['level_name']} ต้องให้ผู้ที่ไม่เคยเป็นผู้รับผิดชอบรายการนี้เป็นผู้ปิดรายการ"]
    return _transition(db, hazard, {"closing_note": note}, "closed", user, "closed", note, notify_event="hazard_closed")


def reject(db, user, hazard_id: int, reason: str) -> list[str]:
    hazard, errors = _open_for(db, user, "hazard.assess", "ปฏิเสธรายการ", hazard_id, OPEN)
    if errors:
        return errors
    reason = (reason or "").strip()
    if not reason:
        return ["กรุณาระบุเหตุผลที่ปฏิเสธ"]
    if len(reason) > NOTE_MAX:
        return [f"เหตุผลยาวเกิน {NOTE_MAX} ตัวอักษร"]
    return _transition(db, hazard, {"reject_reason": reason}, "rejected", user, "rejected", reason, notify_event="hazard_rejected")
