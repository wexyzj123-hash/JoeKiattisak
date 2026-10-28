"""safety/incidents.py — อุบัติเหตุ/Near Miss: รหัส, การมองเห็น, ข้อมูลผู้เกี่ยวข้อง (ข้อมูลส่วนบุคคล), และการแจ้งเหตุ
ชื่อ/รหัสพนักงานของผู้เกี่ยวข้องเห็นได้เฉพาะผู้สอบสวน/ผู้ปิดรายการ/แอดมิน และผู้แจ้งรายการนั้น — ไม่เคยถูกเขียนลงบันทึกเหตุการณ์ การแจ้งเตือน หรือ log"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Mapping

from . import notify, timeutil
from .hazards import hazard_scope_sql, risk_area_options, visible_hazard
from .perms import user_permissions
from .risk import calc_score, level_for_score

INCIDENT_STATUS_TH = {"reported": "แจ้งแล้ว", "investigating": "กำลังสอบสวน", "actions_set": "กำหนดมาตรการแล้ว",
                      "closed": "ปิดแล้ว", "rejected": "ปฏิเสธ"}
INCIDENT_KIND_TH = {"accident": "อุบัติเหตุ", "near_miss": "Near Miss"}
INCIDENT_EVENT_TH = {"reported": "แจ้งเหตุการณ์", "assigned": "มอบหมายผู้สอบสวน", "investigated": "บันทึกผลสอบสวน",
                     "people_updated": "แก้ไขรายชื่อผู้เกี่ยวข้อง", "reclassified": "ปรับระดับความรุนแรง", "closed": "ปิดรายการ", "rejected": "ปฏิเสธรายการ"}
PEOPLE_ROLES = {"injured": "ผู้บาดเจ็บ", "witness": "พยาน", "involved": "ผู้เกี่ยวข้อง"}
OPEN_STATUSES = ("reported", "investigating", "actions_set")
VIEW_ALL_PERMS = {"incident.investigate", "incident.close"}
TITLE_MAX, DESC_MAX, ACTION_MAX = 200, 2000, 1000
NAME_MAX, EMP_MAX, PNOTE_MAX, MAX_PEOPLE, FORM_SLOTS = 120, 30, 300, 20, 40
MAX_AGE_YEARS = 5
NOT_FOUND = ["ไม่พบรายการ"]
LOST_RACE = ["รายการถูกเปลี่ยนสถานะโดยผู้อื่นแล้ว กรุณารีเฟรชหน้า"]


def can_view_all(perms: set[str]) -> bool:
    return bool(perms & VIEW_ALL_PERMS)


def scope_sql(user, alias: str = "i") -> tuple[str, list]:
    sql, args = hazard_scope_sql(user)                       # ใช้เงื่อนไขโรงงาน/แผนกชุดเดียวกับอันตราย (ตัวย่อ h → alias)
    return sql.replace("h.risk_area_id", f"{alias}.risk_area_id"), args


def visible_incident(db, user, incident_id: int):
    """แถวอุบัติเหตุที่ผู้ใช้มองเห็น หรือ None (ไม่มี/นอกขอบเขต/ไม่ใช่ผู้แจ้งหรือผู้สอบสวนของรายการ)"""
    sql, args = scope_sql(user)
    row = db.execute(f"SELECT i.* FROM incidents i WHERE i.id = ? AND {sql}", [incident_id] + args).fetchone()
    if row is None:
        return None
    if not can_view_all(user_permissions(db, user)) and user["id"] not in (row["reported_by"], row["owner_id"]):
        return None
    return row


def can_see_people(db, user, incident) -> bool:
    return bool(user_permissions(db, user) & VIEW_ALL_PERMS) or user["id"] == incident["reported_by"]


def people_for(db, user, incident):
    """รายชื่อผู้เกี่ยวข้อง หรือ None ถ้าผู้ใช้ไม่มีสิทธิ์เห็น"""
    if not can_see_people(db, user, incident):
        return None
    return db.execute("SELECT * FROM incident_people WHERE incident_id = ? ORDER BY id", (incident["id"],)).fetchall()


def people_count(db, incident_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM incident_people WHERE incident_id = ?", (incident_id,)).fetchone()[0]


def next_code(db, today: date | None = None) -> str:
    """IN-YYMM-#### ลำดับเริ่มใหม่ทุกเดือน; ผู้เรียกลองใหม่หนึ่งครั้งถ้าชนกัน (IntegrityError)"""
    today = today or timeutil.today()
    prefix = f"IN-{today:%y%m}-"
    row = db.execute("SELECT code FROM incidents WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (prefix + "%",)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row["code"][len(prefix):]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def add_event(db, incident_id: int, actor_id: int | None, kind: str, from_status: str | None, to_status: str | None,
              note: str = "", data: dict | None = None) -> None:
    """เพิ่มบันทึกเหตุการณ์ (ไม่ commit); data ต้องไม่มีข้อมูลส่วนบุคคล"""
    db.execute("INSERT INTO incident_events(incident_id, actor_id, kind, from_status, to_status, note, data) VALUES(?,?,?,?,?,?,?)",
               (incident_id, actor_id, kind, from_status, to_status, note, json.dumps(data, ensure_ascii=False) if data else ""))


def parse_occurred(raw) -> datetime | None:
    text = str(raw or "").strip().replace("T", " ")
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _years_ago(moment: datetime, years: int) -> datetime:
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:                                   # 29 ก.พ. → ปีที่ไม่มีวันนี้ ใช้ 28 ก.พ.
        return moment.replace(year=moment.year - years, day=28)


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_people(form: Mapping) -> tuple[list[dict], list[str]]:
    """อ่านแถวผู้เกี่ยวข้องจากฟอร์ม (people_name_N ...); ข้ามแถวที่ไม่มีชื่อ; ตรวจความยาว/จำนวน/บทบาท"""
    people, errors = [], []
    for n in range(FORM_SLOTS):
        name = (form.get(f"people_name_{n}") or "").strip()
        if not name:
            continue
        role = (form.get(f"people_role_{n}") or "").strip() or "injured"
        emp, note = (form.get(f"people_no_{n}") or "").strip(), (form.get(f"people_note_{n}") or "").strip()
        if role not in PEOPLE_ROLES:
            errors.append("บทบาทของผู้เกี่ยวข้องไม่ถูกต้อง")
        if len(name) > NAME_MAX:
            errors.append(f"ชื่อผู้เกี่ยวข้องยาวเกิน {NAME_MAX} ตัวอักษร")
        if len(emp) > EMP_MAX:
            errors.append(f"รหัสพนักงานยาวเกิน {EMP_MAX} ตัวอักษร")
        if len(note) > PNOTE_MAX:
            errors.append(f"หมายเหตุของผู้เกี่ยวข้องยาวเกิน {PNOTE_MAX} ตัวอักษร")
        people.append({"role": role, "name": name, "employee_no": emp, "note": note})
    if len(people) > MAX_PEOPLE:
        errors.append(f"ระบุผู้เกี่ยวข้องได้ไม่เกิน {MAX_PEOPLE} คน")
    return people, sorted(set(errors))


def report(db, user, form: Mapping) -> tuple[int | None, list[str]]:
    if "incident.report" not in user_permissions(db, user):
        return None, ["คุณไม่มีสิทธิ์แจ้งอุบัติเหตุ"]
    errors: list[str] = []
    kind = (form.get("kind") or "").strip()
    if kind not in INCIDENT_KIND_TH:
        errors.append("กรุณาเลือกประเภทเหตุการณ์ (อุบัติเหตุ หรือ Near Miss)")
    title, desc, action = (form.get("title") or "").strip(), (form.get("description") or "").strip(), (form.get("immediate_action") or "").strip()
    if not title:
        errors.append("กรุณากรอกหัวข้อเหตุการณ์")
    elif len(title) > TITLE_MAX:
        errors.append(f"หัวข้อยาวเกิน {TITLE_MAX} ตัวอักษร")
    if len(desc) > DESC_MAX:
        errors.append(f"รายละเอียดยาวเกิน {DESC_MAX} ตัวอักษร")
    if len(action) > ACTION_MAX:
        errors.append(f"การแก้ไขเบื้องต้นยาวเกิน {ACTION_MAX} ตัวอักษร")
    now = timeutil.now()
    occurred = parse_occurred(form.get("occurred_at"))
    if occurred is None:
        errors.append("กรุณาระบุวันเวลาที่เกิดเหตุให้ถูกต้อง")
    elif occurred > now:
        errors.append("วันเวลาที่เกิดเหตุต้องไม่อยู่ในอนาคต")
    elif occurred < _years_ago(now, MAX_AGE_YEARS):
        errors.append(f"วันเวลาที่เกิดเหตุต้องไม่เกิน {MAX_AGE_YEARS} ปีย้อนหลัง")
    area = _int(form.get("risk_area_id"))
    if area is None or area not in {o["id"] for o in risk_area_options(db, user)}:
        errors.append("กรุณาเลือกพื้นที่เสี่ยงที่ใช้งานอยู่และอยู่ในขอบเขตของคุณ")
    sev_id, snapshot = None, {}
    if kind == "accident":
        sev_id = _int(form.get("injury_severity_id"))
        if sev_id is None or not db.execute("SELECT 1 FROM injury_severities WHERE id=? AND active=1", (sev_id,)).fetchone():
            errors.append("กรุณาเลือกระดับความรุนแรงของการบาดเจ็บ")
            sev_id = None
    elif kind == "near_miss":
        lik, sev = _int(form.get("likelihood")), _int(form.get("severity"))
        if lik is None or sev is None:
            errors.append("Near Miss ต้องประเมินโอกาสและความรุนแรงที่อาจเกิดขึ้น")
        else:
            try:
                score = calc_score(db, lik, sev)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                level = level_for_score(db, score)
                if level is None:
                    errors.append("คะแนนนี้ไม่มีระดับความเสี่ยงรองรับ กรุณาแจ้งผู้ดูแลระบบตรวจเกณฑ์ความเสี่ยง")
                else:
                    snapshot = {"likelihood": lik, "severity": sev, "score": score, "risk_level_id": level["id"],
                                "level_name": level["name_th"], "level_color": level["color"],
                                "level_requires_approval": level["requires_approval"]}
    related = None
    raw_rel = (form.get("related_hazard_id") or "").strip()
    if raw_rel:
        related = _int(raw_rel)
        if related is None or visible_hazard(db, user, related) is None:
            errors.append("ไม่พบรายการอันตรายที่เกี่ยวข้อง")
    people, perrors = parse_people(form)
    errors += perrors
    if errors:
        return None, errors
    cols = {"kind": kind, "occurred_at": occurred.strftime("%Y-%m-%d %H:%M"), "risk_area_id": area, "title": title, "description": desc,
            "immediate_action": action, "injury_severity_id": sev_id, "reported_by": user["id"], "related_hazard_id": related, **snapshot}
    for attempt in (1, 2):
        try:
            cur = db.execute(f"INSERT INTO incidents(code,{','.join(cols)}) VALUES(?,{','.join('?' * len(cols))})", [next_code(db), *cols.values()])
            break
        except sqlite3.IntegrityError:                  # รหัสชนกันตอนสร้างพร้อมกัน — คำนวณใหม่แล้วลองอีกครั้ง
            db.rollback()
            if attempt == 2:
                return None, ["สร้างรหัสรายการไม่สำเร็จ กรุณาลองอีกครั้ง"]
    iid = cur.lastrowid
    db.executemany("INSERT INTO incident_people(incident_id, role, name, employee_no, note) VALUES(?,?,?,?,?)",
                   [(iid, p["role"], p["name"], p["employee_no"], p["note"]) for p in people])
    add_event(db, iid, user["id"], "reported", None, "reported", data={"kind": kind, "people": len(people)})
    notify.emit_incident(db, "incident_reported", iid, user["id"])
    db.commit()
    return iid, []
