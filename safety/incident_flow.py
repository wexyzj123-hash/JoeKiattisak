"""safety/incident_flow.py — ขั้นตอนอุบัติเหตุ: แจ้ง → มอบหมายผู้สอบสวน → บันทึกผลสอบสวน/มาตรการ → ปิด (หรือปฏิเสธ)
ทุกขั้นตรวจสิทธิ์ → การมองเห็น → สถานะ → ข้อมูล แล้วเปลี่ยนสถานะ + บันทึกเหตุการณ์ + แจ้งเตือนใน transaction เดียว
ใช้ UPDATE ... WHERE status=? AND version=? (optimistic lock) เหมือนรายการอันตราย"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Mapping

from . import capa, notify, timeutil
from .hazards import user_in_scope
from .incidents import (LOST_RACE, MAX_PEOPLE, NOT_FOUND, OPEN_STATUSES, add_event, parse_people, visible_incident)
from .perms import user_permissions

NOTE_MAX, TEXT_MAX, LOST_DAYS_MAX = 1000, 2000, 3650
DEFAULT_DUE_DAYS = 14
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STALE_FORM = ["ข้อมูลรายการนี้ถูกแก้ไขโดยผู้อื่นแล้ว กรุณารีเฟรชหน้าแล้วทำรายการใหม่"]


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _open_for(db, user, perm: str, what: str, incident_id, allowed: tuple[str, ...]):
    """ตรวจสิทธิ์ + การมองเห็น + สถานะ; คืน (incident, errors)"""
    if perm not in user_permissions(db, user):
        return None, [f"คุณไม่มีสิทธิ์{what}"]
    inc = visible_incident(db, user, incident_id)
    if inc is None:
        return None, NOT_FOUND
    if inc["status"] not in allowed:
        return None, ["สถานะของรายการนี้ไม่อนุญาตให้ทำรายการดังกล่าว"]
    return inc, []


def _transition(db, inc, sets: dict, kind: str, user, to_status: str, note: str = "", data: dict | None = None,
                notify_event: str | None = None, guard=None) -> list[str]:
    cols = ", ".join(f"{c}=?" for c in sets)
    cur = db.execute(f"UPDATE incidents SET {cols}, status=?, version=version+1, updated_at=datetime('now','localtime') "
                     "WHERE id=? AND status=? AND version=?",
                     [*sets.values(), to_status, inc["id"], inc["status"], inc["version"]])
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    add_event(db, inc["id"], user["id"], kind, inc["status"], to_status, note, data)
    if guard is not None:                                              # ตรวจหลังได้ล็อกเขียนแล้ว (ธุรกรรมเดียวกัน)
        blocked = guard()
        if blocked:
            db.rollback()
            return blocked
    if notify_event:
        notify.emit_incident(db, notify_event, inc["id"], user["id"])
    db.commit()
    return []


def investigators_ever(db, inc) -> set[int]:
    """ผู้สอบสวนปัจจุบันและทุกคนที่เคยเป็น (จากประวัติ) — โอนงานออกแล้วก็ยังปิดรายการเองไม่ได้"""
    found = {inc["owner_id"]} - {None}
    for row in db.execute("SELECT data FROM incident_events WHERE incident_id=? AND kind='assigned'", (inc["id"],)):
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            continue
        found |= {data.get("owner"), data.get("old_owner")} - {None}
    for row in db.execute("SELECT actor_id FROM incident_events WHERE incident_id=? AND kind='investigated'", (inc["id"],)):
        found |= {row["actor_id"]} - {None}                # แอดมินที่บันทึกผลสอบสวนแทนก็นับเป็นผู้สอบสวน (four-eyes)
    return found


def assign_investigator(db, user, incident_id: int, owner_id, due_date: str, note: str = "") -> list[str]:
    inc, errors = _open_for(db, user, "incident.investigate", "มอบหมายผู้สอบสวน", incident_id, ("reported", "investigating"))
    if errors:
        return errors
    owner = _int(owner_id)
    row = db.execute("SELECT * FROM safety_users WHERE id=? AND active=1", (owner,)).fetchone() if owner is not None else None
    if row is None:
        return ["กรุณาเลือกผู้สอบสวนที่ใช้งานอยู่"]
    if "incident.investigate" not in user_permissions(db, row):
        return ["ผู้ที่เลือกไม่มีสิทธิ์สอบสวนอุบัติเหตุ"]
    if not user_in_scope(db, row, inc["risk_area_id"]):
        return ["ผู้สอบสวนที่เลือกอยู่นอกขอบเขตของรายการนี้"]
    today, raw = timeutil.today(), (due_date or "").strip()
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
        due = today + timedelta(days=DEFAULT_DUE_DAYS)
    return _transition(db, inc, {"owner_id": owner, "due_date": due.isoformat()}, "assigned", user, "investigating",
                       (note or "").strip()[:NOTE_MAX], {"old_owner": inc["owner_id"], "owner": owner, "due_date": due.isoformat()},
                       "incident_assigned")


def record_investigation(db, user, incident_id: int, cause_id, root_cause: str, corrective_action: str, lost_days,
                         expected_version: int | None = None) -> list[str]:
    inc, errors = _open_for(db, user, "incident.investigate", "บันทึกผลสอบสวน", incident_id, ("investigating", "actions_set"))
    if errors:
        return errors
    if expected_version is not None and expected_version != inc["version"]:
        return STALE_FORM
    if user["id"] != inc["owner_id"] and user["role"] != "admin":
        return ["เฉพาะผู้สอบสวนที่ได้รับมอบหมายหรือผู้ดูแลระบบเท่านั้นที่บันทึกผลสอบสวนได้"]
    cause = _int(cause_id)
    if cause is None or not db.execute("SELECT 1 FROM incident_causes WHERE id=? AND active=1", (cause,)).fetchone():
        return ["กรุณาเลือกสาเหตุหลักที่ใช้งานอยู่"]
    root, action = (root_cause or "").strip(), (corrective_action or "").strip()
    if not root or not action:
        return ["กรุณากรอกสาเหตุรากและมาตรการแก้ไขป้องกัน"]
    if len(root) > TEXT_MAX or len(action) > TEXT_MAX:
        return [f"สาเหตุรากและมาตรการยาวเกิน {TEXT_MAX} ตัวอักษร"]
    lost = None
    needs_lost = inc["kind"] == "accident" and db.execute(
        "SELECT counts_lost_time FROM injury_severities WHERE id=?", (inc["injury_severity_id"],)).fetchone()
    raw_lost = "" if lost_days is None else str(lost_days).strip()
    if needs_lost and needs_lost[0]:
        lost = _int(raw_lost)
        if lost is None or not 0 <= lost <= LOST_DAYS_MAX:
            return [f"อุบัติเหตุหยุดงานต้องระบุจำนวนวันที่หยุดงาน (0-{LOST_DAYS_MAX})"]
    elif raw_lost:
        lost = _int(raw_lost)
        if lost is None or not 0 <= lost <= LOST_DAYS_MAX:
            return [f"จำนวนวันที่หยุดงานต้องเป็นจำนวนเต็ม 0-{LOST_DAYS_MAX}"]
    return _transition(db, inc, {"cause_id": cause, "root_cause": root, "corrective_action": action, "lost_days": lost},
                       "investigated", user, "actions_set", data={"cause_id": cause, "lost_days": lost})


def close(db, user, incident_id: int, note: str) -> list[str]:
    inc, errors = _open_for(db, user, "incident.close", "ปิดรายการอุบัติเหตุ", incident_id, ("actions_set",))
    if errors:
        return errors
    note = (note or "").strip()
    if not note:
        return ["กรุณาระบุผลการแก้ไข/สรุปการปิดรายการ"]
    if len(note) > NOTE_MAX:
        return [f"ผลการแก้ไขยาวเกิน {NOTE_MAX} ตัวอักษร"]
    if user["id"] in investigators_ever(db, inc):
        return ["ผู้ที่เคยเป็นผู้สอบสวนรายการนี้ปิดรายการเองไม่ได้ ต้องให้ผู้อื่นที่มีสิทธิ์ปิดเป็นผู้ปิด"]
    return _transition(db, inc, {"closing_note": note}, "closed", user, "closed", note, notify_event="incident_closed",
                       guard=lambda: capa.gate_errors(db, "incident", inc["id"]))


def reject(db, user, incident_id: int, reason: str) -> list[str]:
    inc, errors = _open_for(db, user, "incident.investigate", "ปฏิเสธรายการ", incident_id, OPEN_STATUSES)
    if errors:
        return errors
    reason = (reason or "").strip()
    if not reason:
        return ["กรุณาระบุเหตุผลที่ปฏิเสธ"]
    if len(reason) > 500:
        return ["เหตุผลยาวเกิน 500 ตัวอักษร"]
    return _transition(db, inc, {"reject_reason": reason}, "rejected", user, "rejected", reason)


def update_people(db, user, incident_id: int, form: Mapping) -> list[str]:
    inc, errors = _open_for(db, user, "incident.investigate", "แก้ไขรายชื่อผู้เกี่ยวข้อง", incident_id, OPEN_STATUSES)
    if errors:
        return errors
    if _int(form.get("version")) is not None and _int(form.get("version")) != inc["version"]:
        return STALE_FORM
    people, perrors = parse_people(form)
    if perrors:
        return perrors
    cur = db.execute("UPDATE incidents SET version=version+1, updated_at=datetime('now','localtime') WHERE id=? AND status=? AND version=?",
                     (inc["id"], inc["status"], inc["version"]))
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    db.execute("DELETE FROM incident_people WHERE incident_id=?", (inc["id"],))
    db.executemany("INSERT INTO incident_people(incident_id, role, name, employee_no, note) VALUES(?,?,?,?,?)",
                   [(inc["id"], p["role"], p["name"], p["employee_no"], p["note"]) for p in people])
    add_event(db, inc["id"], user["id"], "people_updated", inc["status"], inc["status"], data={"count": len(people)})
    db.commit()
    return []


def reclassify(db, user, incident_id: int, severity_id, reason: str, lost_days, expected_version: int | None = None) -> list[str]:
    """ปรับระดับความรุนแรงของการบาดเจ็บหลังแจ้ง (เช่น ปฐมพยาบาล → หยุดงาน) พร้อมเหตุผลและประวัติ; ใช้ได้กับอุบัติเหตุที่ยังไม่ถูกปฏิเสธ (รวมที่ปิดแล้ว)
    ไม่เปลี่ยนสถานะ; ประวัติเก็บรหัสระดับเดิม/ใหม่ + เหตุผล (ไม่มีข้อมูลบุคคล); ตัวนับวันปลอดอุบัติเหตุตามระดับใหม่ทันที"""
    inc, errors = _open_for(db, user, "incident.investigate", "ปรับระดับความรุนแรง", incident_id, (*OPEN_STATUSES, "closed"))
    if errors:
        return errors
    if expected_version is not None and expected_version != inc["version"]:
        return STALE_FORM
    if inc["kind"] != "accident":
        return ["Near Miss ไม่มีระดับการบาดเจ็บให้ปรับ"]
    new = _int(severity_id)
    sev = db.execute("SELECT * FROM injury_severities WHERE id=? AND active=1", (new,)).fetchone() if new is not None else None
    if sev is None:
        return ["กรุณาเลือกระดับความรุนแรงที่ใช้งานอยู่"]
    if new == inc["injury_severity_id"]:
        return ["ระดับที่เลือกเหมือนเดิม"]
    reason = (reason or "").strip()
    if not reason:
        return ["กรุณาระบุเหตุผลที่ปรับระดับ"]
    if len(reason) > 500:
        return ["เหตุผลยาวเกิน 500 ตัวอักษร"]
    raw = "" if lost_days is None else str(lost_days).strip()
    lost = inc["lost_days"]
    if raw or sev["counts_lost_time"]:
        lost = _int(raw)
        if lost is None or not 0 <= lost <= LOST_DAYS_MAX:
            return [f"ระดับหยุดงานต้องระบุจำนวนวันที่หยุดงาน (0-{LOST_DAYS_MAX})"]
    cur = db.execute("UPDATE incidents SET injury_severity_id=?, lost_days=?, version=version+1, updated_at=datetime('now','localtime') "
                     "WHERE id=? AND status=? AND version=?", (new, lost, inc["id"], inc["status"], inc["version"]))
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    add_event(db, inc["id"], user["id"], "reclassified", inc["status"], inc["status"], reason,
              {"old": inc["injury_severity_id"], "new": new, "lost_days": lost})
    db.commit()
    return []
