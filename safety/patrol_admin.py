"""safety/patrol_admin.py — แบบตรวจ (checklist) รายการตรวจ และแผนการตรวจ: เพิ่ม/แก้/ปิดใช้งานเท่านั้น (ไม่มีการลบ — รอบตรวจเก่าอ้างอิงอยู่)
ทุกฟังก์ชันต้องมีสิทธิ์ patrol.manage; คืนรายการข้อความผิดพลาดภาษาไทย (ว่าง = สำเร็จ)"""
from __future__ import annotations

import re
import sqlite3
from typing import Mapping

from .hazards import risk_area_options, user_in_scope
from .perms import user_permissions

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NAME_MAX, DESC_MAX, SECTION_MAX, TEXT_MAX = 200, 1000, 100, 300
FREQUENCIES = {"daily": "รายวัน", "weekly": "รายสัปดาห์", "monthly": "รายเดือน"}
DENIED = ["คุณไม่มีสิทธิ์จัดการแบบตรวจ"]


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _flag(value) -> int:
    return 1 if value not in (None, "", "0") else 0


def _allowed(db, user) -> bool:
    return "patrol.manage" in user_permissions(db, user)


def create_checklist(db, user, form: Mapping) -> tuple[int | None, list[str]]:
    if not _allowed(db, user):
        return None, DENIED
    code, name, desc = (form.get("code") or "").strip(), (form.get("name_th") or "").strip(), (form.get("description") or "").strip()
    errors = []
    if not CODE_RE.match(code):
        errors.append("รหัสแบบตรวจใช้ได้เฉพาะ A-Z a-z 0-9 _ - ยาว 1-32 ตัว")
    if not name or len(name) > NAME_MAX:
        errors.append(f"กรุณากรอกชื่อแบบตรวจ (ไม่เกิน {NAME_MAX} ตัวอักษร)")
    if len(desc) > DESC_MAX:
        errors.append(f"คำอธิบายยาวเกิน {DESC_MAX} ตัวอักษร")
    if errors:
        return None, errors
    try:
        cur = db.execute("INSERT INTO checklists(code, name_th, description, created_by) VALUES(?,?,?,?)", (code, name, desc, user["id"]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return None, ["มีรหัสแบบตรวจนี้อยู่แล้ว"]
    return cur.lastrowid, []


def update_checklist(db, user, checklist_id: int, form: Mapping) -> list[str]:
    if not _allowed(db, user):
        return DENIED
    name, desc = (form.get("name_th") or "").strip(), (form.get("description") or "").strip()
    if db.execute("SELECT 1 FROM checklists WHERE id=?", (checklist_id,)).fetchone() is None:
        return ["ไม่พบแบบตรวจ"]
    if not name or len(name) > NAME_MAX:
        return [f"กรุณากรอกชื่อแบบตรวจ (ไม่เกิน {NAME_MAX} ตัวอักษร)"]
    if len(desc) > DESC_MAX:
        return [f"คำอธิบายยาวเกิน {DESC_MAX} ตัวอักษร"]
    db.execute("UPDATE checklists SET name_th=?, description=? WHERE id=?", (name, desc, checklist_id))
    db.commit()
    return []


def toggle_checklist(db, user, checklist_id: int) -> list[str]:
    if not _allowed(db, user):
        return DENIED
    if db.execute("UPDATE checklists SET active = 1 - active WHERE id=?", (checklist_id,)).rowcount != 1:
        db.rollback()
        return ["ไม่พบแบบตรวจ"]
    db.commit()
    return []


def _item_fields(form: Mapping) -> tuple[dict, list[str]]:
    section, text = (form.get("section") or "").strip(), (form.get("text_th") or "").strip()
    weight = _int(form.get("weight")) if (form.get("weight") or "").strip() else 1
    sort = _int(form.get("sort")) if (form.get("sort") or "").strip() else 0
    errors = []
    if not text or len(text) > TEXT_MAX:
        errors.append(f"กรุณากรอกข้อความรายการตรวจ (ไม่เกิน {TEXT_MAX} ตัวอักษร)")
    if len(section) > SECTION_MAX:
        errors.append(f"ชื่อหมวดยาวเกิน {SECTION_MAX} ตัวอักษร")
    if weight is None or not 1 <= weight <= 10:
        errors.append("น้ำหนักคะแนนต้องเป็นจำนวนเต็ม 1-10")
    if sort is None or not -9999 <= sort <= 9999:
        errors.append("ลำดับต้องเป็นจำนวนเต็ม -9999 ถึง 9999")
    return {"section": section, "text_th": text, "weight": weight, "sort": sort, "require_photo_on_fail": _flag(form.get("require_photo_on_fail"))}, errors


def add_item(db, user, checklist_id: int, form: Mapping) -> tuple[int | None, list[str]]:
    if not _allowed(db, user):
        return None, DENIED
    if db.execute("SELECT 1 FROM checklists WHERE id=?", (checklist_id,)).fetchone() is None:
        return None, ["ไม่พบแบบตรวจ"]
    f, errors = _item_fields(form)
    if errors:
        return None, errors
    cur = db.execute("INSERT INTO checklist_items(checklist_id, section, text_th, require_photo_on_fail, weight, sort) VALUES(?,?,?,?,?,?)",
                     (checklist_id, f["section"], f["text_th"], f["require_photo_on_fail"], f["weight"], f["sort"]))
    db.commit()
    return cur.lastrowid, []


def update_item(db, user, item_id: int, form: Mapping) -> list[str]:
    if not _allowed(db, user):
        return DENIED
    if db.execute("SELECT 1 FROM checklist_items WHERE id=?", (item_id,)).fetchone() is None:
        return ["ไม่พบรายการตรวจ"]
    f, errors = _item_fields(form)
    if errors:
        return errors
    db.execute("UPDATE checklist_items SET section=?, text_th=?, require_photo_on_fail=?, weight=?, sort=? WHERE id=?",
               (f["section"], f["text_th"], f["require_photo_on_fail"], f["weight"], f["sort"], item_id))
    db.commit()
    return []


def toggle_item(db, user, item_id: int) -> list[str]:
    if not _allowed(db, user):
        return DENIED
    if db.execute("UPDATE checklist_items SET active = 1 - active WHERE id=?", (item_id,)).rowcount != 1:
        db.rollback()
        return ["ไม่พบรายการตรวจ"]
    db.commit()
    return []


def _inspector(db, raw, area_id: int) -> tuple[int | None, list[str]]:
    if not (raw or "").strip():
        return None, []
    uid = _int(raw)
    row = db.execute("SELECT * FROM safety_users WHERE id=? AND active=1", (uid,)).fetchone() if uid is not None else None
    if row is None:
        return None, ["กรุณาเลือกผู้ตรวจประจำแผนที่ใช้งานอยู่"]
    if "patrol.perform" not in user_permissions(db, row):
        return None, ["ผู้ตรวจที่เลือกไม่มีสิทธิ์ตรวจความปลอดภัย"]
    if not user_in_scope(db, row, area_id):
        return None, ["ผู้ตรวจที่เลือกอยู่นอกขอบเขตของพื้นที่นี้"]
    return uid, []


def create_plan(db, user, form: Mapping) -> tuple[int | None, list[str]]:
    if not _allowed(db, user):
        return None, DENIED
    cid, area, freq = _int(form.get("checklist_id")), _int(form.get("risk_area_id")), (form.get("frequency") or "").strip()
    if cid is None or db.execute("SELECT 1 FROM checklists WHERE id=? AND active=1", (cid,)).fetchone() is None:
        return None, ["กรุณาเลือกแบบตรวจที่ใช้งานอยู่"]
    if area is None or area not in {o["id"] for o in risk_area_options(db, user)}:
        return None, ["กรุณาเลือกพื้นที่เสี่ยงที่ใช้งานอยู่และอยู่ในขอบเขตของคุณ"]
    if freq not in FREQUENCIES:
        return None, ["ความถี่ต้องเป็นรายวัน รายสัปดาห์ หรือรายเดือน"]
    inspector, errors = _inspector(db, form.get("inspector_id"), area)
    if errors:
        return None, errors
    try:
        cur = db.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency, inspector_id, created_by) VALUES(?,?,?,?,?)",
                         (cid, area, freq, inspector, user["id"]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return None, ["มีแผนการตรวจของแบบตรวจนี้ในพื้นที่นี้ที่ใช้งานอยู่แล้ว"]
    return cur.lastrowid, []


def update_plan(db, user, plan_id: int, form: Mapping) -> list[str]:
    if not _allowed(db, user):
        return DENIED
    plan = db.execute("SELECT * FROM patrol_plans WHERE id=?", (plan_id,)).fetchone()
    if plan is None:
        return ["ไม่พบแผนการตรวจ"]
    freq = (form.get("frequency") or "").strip()
    if freq not in FREQUENCIES:
        return ["ความถี่ต้องเป็นรายวัน รายสัปดาห์ หรือรายเดือน"]
    inspector, errors = _inspector(db, form.get("inspector_id"), plan["risk_area_id"])
    if errors:
        return errors
    db.execute("UPDATE patrol_plans SET frequency=?, inspector_id=? WHERE id=?", (freq, inspector, plan_id))
    db.commit()
    return []


def toggle_plan(db, user, plan_id: int) -> list[str]:
    if not _allowed(db, user):
        return DENIED
    try:
        if db.execute("UPDATE patrol_plans SET active = 1 - active WHERE id=?", (plan_id,)).rowcount != 1:
            db.rollback()
            return ["ไม่พบแผนการตรวจ"]
        db.commit()
    except sqlite3.IntegrityError:                         # เปิดแผนเก่ากลับตอนมีแผนใช้งานของคู่เดียวกันอยู่แล้ว
        db.rollback()
        return ["มีแผนการตรวจของแบบตรวจนี้ในพื้นที่นี้ที่ใช้งานอยู่แล้ว — ปิดแผนนั้นก่อน"]
    return []
