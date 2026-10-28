"""safety/capa.py — CAPA (การแก้ไข/ป้องกัน): เปิด → กำลังทำ → เสร็จ → ยืนยัน (หรือยกเลิก/ตีกลับ), แหล่งที่มา hazard|incident|patrol|manual
ทุกขั้นตรวจสิทธิ์ → การมองเห็น → สถานะ → ข้อมูล แล้วเปลี่ยนสถานะ + บันทึกเหตุการณ์ + แจ้งเตือนใน transaction เดียว (optimistic lock)
four-eyes: ผู้ยืนยันต้องไม่เคยเป็นเจ้าของ (ปัจจุบัน/อดีต) และไม่ใช่ผู้ที่กดว่าเสร็จ"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Mapping

from . import notify, timeutil
from .hazards import hazard_scope_sql, risk_area_options, user_in_scope, visible_hazard
from .incidents import LOST_RACE, NOT_FOUND, visible_incident
from .perms import user_permissions

STATUS_TH = {"open": "เปิด", "in_progress": "กำลังดำเนินการ", "done": "เสร็จ รอยืนยัน", "verified": "ยืนยันแล้ว", "cancelled": "ยกเลิก"}
KIND_TH = {"hazard": "รายการอันตราย", "incident": "อุบัติเหตุ/Near Miss", "patrol": "ข้อไม่ผ่านจากการตรวจ", "manual": "สร้างเอง"}
TYPE_TH = {"corrective": "แก้ไข (Corrective)", "preventive": "ป้องกัน (Preventive)"}
EVENT_TH = {"created": "สร้าง CAPA", "started": "เริ่มดำเนินการ", "completed": "ดำเนินการเสร็จ", "verified": "ยืนยันผล", "reopened": "ตีกลับให้แก้ไข",
            "cancelled": "ยกเลิก", "reassigned": "เปลี่ยนเจ้าของ/กำหนดส่ง", "effectiveness": "บันทึกผลตรวจประสิทธิผล"}
OPEN_STATES = ("open", "in_progress")
TITLE_MAX, TEXT_MAX, NOTE_MAX, MAX_DAYS = 200, 2000, 500, 730
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STALE_FORM = ["ข้อมูล CAPA นี้ถูกแก้ไขโดยผู้อื่นแล้ว กรุณารีเฟรชหน้าแล้วทำรายการใหม่"]
CREATORS = {"capa.create"}


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _date(raw, *, allow_blank=False, lo: date | None = None):
    """(date | None, error | None) — วันที่รูปแบบ ปปปป-ดด-วว ที่มีอยู่จริง ตั้งแต่วันนี้ถึง +730 วัน"""
    raw = (raw or "").strip()
    if not raw:
        return None, None if allow_blank else "กรุณาระบุวันที่"
    try:
        if not DATE_RE.match(raw):
            raise ValueError
        d = date.fromisoformat(raw)
    except ValueError:
        return None, "วันที่ต้องเป็นรูปแบบ ปปปป-ดด-วว ที่มีอยู่จริง"
    today = timeutil.today()
    if d < (lo or today):
        return None, "วันที่ต้องไม่เป็นวันในอดีต"
    if d > today + timedelta(days=MAX_DAYS):
        return None, "วันที่ต้องไม่เกิน 2 ปีนับจากวันนี้"
    return d, None


def scope_sql(user, alias: str = "c") -> tuple[str, list]:
    sql, args = hazard_scope_sql(user)
    return sql.replace("h.risk_area_id", f"{alias}.risk_area_id"), list(args)


def visible_capa(db, user, capa_id: int):
    sql, args = scope_sql(user)
    row = db.execute(f"SELECT c.* FROM capas c WHERE c.id=? AND {sql}", [capa_id] + args).fetchone()
    if row is None:
        return None
    if not user_permissions(db, user) & {"capa.create", "capa.verify"} and user["id"] not in (row["owner_id"], row["created_by"]):
        return None
    return row


def add_event(db, capa_id: int, actor_id: int | None, kind: str, from_status: str | None, to_status: str | None, note: str = "", data: dict | None = None) -> None:
    db.execute("INSERT INTO capa_events(capa_id, actor_id, kind, from_status, to_status, note, data) VALUES(?,?,?,?,?,?,?)",
               (capa_id, actor_id, kind, from_status, to_status, note, json.dumps(data, ensure_ascii=False) if data else ""))


def _next_code(db, today: date) -> str:
    prefix = f"CP-{today:%y%m}-"
    row = db.execute("SELECT code FROM capas WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (prefix + "%",)).fetchone()
    try:
        seq = int(row["code"][len(prefix):]) + 1 if row else 1
    except ValueError:
        seq = 1
    return f"{prefix}{seq:04d}"


def owners_ever(db, capa) -> set[int]:
    """เจ้าของปัจจุบันและทุกคนที่เคยเป็น (จากประวัติ) — โอนงานออกแล้วก็ยังยืนยันผลเองไม่ได้"""
    found = {capa["owner_id"]} - {None}
    for row in db.execute("SELECT data FROM capa_events WHERE capa_id=? AND kind IN ('created','reassigned')", (capa["id"],)):
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            continue
        found |= {data.get("owner"), data.get("old_owner")} - {None}
    return found


def _source(db, user, source) -> tuple[int | None, list[str]]:
    """พื้นที่ของแหล่งที่มา (ต้องเป็นรายการที่ผู้ใช้มองเห็น)"""
    kind, sid = source
    if kind == "hazard":
        row = visible_hazard(db, user, _int(sid) or 0)
    elif kind == "incident":
        row = visible_incident(db, user, _int(sid) or 0)
    elif kind == "patrol":
        from . import patrol
        ans = db.execute("SELECT a.id, a.result, a.round_id FROM patrol_answers a WHERE a.id=?", (_int(sid) or 0,)).fetchone()
        rnd = patrol.visible_round(db, user, ans["round_id"]) if ans else None
        row = rnd if rnd is not None and rnd["status"] == "submitted" and ans["result"] == "fail" else None
    else:
        return None, ["แหล่งที่มาของ CAPA ไม่ถูกต้อง"]
    if row is None:
        return None, ["ไม่พบรายการต้นทางของ CAPA"]
    return row["risk_area_id"], []


def create(db, user, form: Mapping, source: tuple[str, int] | None = None, *, require_permission: bool = True) -> tuple[int | None, list[str]]:
    """สร้าง CAPA; require_permission=False ใช้เฉพาะเส้นทางที่ตรวจสิทธิ์เองแล้ว (ข้อไม่ผ่านจากการตรวจของผู้ตรวจ)"""
    if require_permission and "capa.create" not in user_permissions(db, user):
        return None, ["คุณไม่มีสิทธิ์สร้าง CAPA"]
    errors: list[str] = []
    title, desc = (form.get("title") or "").strip(), (form.get("description") or "").strip()
    if not title:
        errors.append("กรุณากรอกหัวข้อ CAPA")
    elif len(title) > TITLE_MAX:
        errors.append(f"หัวข้อยาวเกิน {TITLE_MAX} ตัวอักษร")
    if len(desc) > TEXT_MAX:
        errors.append(f"รายละเอียดยาวเกิน {TEXT_MAX} ตัวอักษร")
    action = (form.get("action_type") or "corrective").strip()
    if action not in TYPE_TH:
        errors.append("ประเภทการดำเนินการต้องเป็นแก้ไขหรือป้องกัน")
    due, derr = _date(form.get("due_date"))
    if derr:
        errors.append("วันครบกำหนด: " + derr)
    kind, sid, area = "manual", None, None
    if source is not None:
        kind, sid = source
        area, serr = _source(db, user, source)
        errors += serr
    else:
        area = _int(form.get("risk_area_id"))
        if area is None or area not in {o["id"] for o in risk_area_options(db, user)}:
            errors.append("กรุณาเลือกพื้นที่เสี่ยงที่ใช้งานอยู่และอยู่ในขอบเขตของคุณ")
    owner = _int(form.get("owner_id"))
    owner_row = db.execute("SELECT * FROM safety_users WHERE id=? AND active=1", (owner,)).fetchone() if owner is not None else None
    if owner_row is None:
        errors.append("กรุณาเลือกเจ้าของงานที่ใช้งานอยู่")
    elif area is not None and not errors and not user_in_scope(db, owner_row, area):
        errors.append("เจ้าของงานที่เลือกอยู่นอกขอบเขตของพื้นที่นี้")
    if errors:
        return None, errors
    for attempt in (1, 2):
        try:
            cur = db.execute("INSERT INTO capas(code, source_kind, source_id, action_type, title, description, risk_area_id, owner_id, due_date, created_by) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?)", (_next_code(db, timeutil.today()), kind, sid, action, title, desc, area, owner,
                                                             due.isoformat(), user["id"]))
            break
        except sqlite3.IntegrityError:
            db.rollback()
            if attempt == 2:
                return None, ["สร้าง CAPA ไม่สำเร็จ (อาจมี CAPA ของข้อนี้อยู่แล้ว) กรุณาลองอีกครั้ง"]
    add_event(db, cur.lastrowid, user["id"], "created", None, "open", data={"owner": owner, "due_date": due.isoformat(), "source": kind})
    notify.emit_capa(db, "capa_assigned", cur.lastrowid, user["id"])
    db.commit()
    return cur.lastrowid, []


def _open_for(db, user, capa_id, allowed: tuple[str, ...]):
    row = visible_capa(db, user, _int(capa_id) or 0)
    if row is None:
        return None, NOT_FOUND
    if row["status"] not in allowed:
        return None, ["สถานะของ CAPA นี้ไม่อนุญาตให้ทำรายการดังกล่าว"]
    return row, []


def _transition(db, capa, sets: dict, kind: str, user, to_status: str, note: str = "", data: dict | None = None,
                notify_event: str | None = None) -> list[str]:
    cols = ", ".join(f"{c}=?" for c in sets)
    cur = db.execute(f"UPDATE capas SET {cols + ', ' if cols else ''}status=?, version=version+1, updated_at=datetime('now','localtime') "
                     "WHERE id=? AND status=? AND version=?", [*sets.values(), to_status, capa["id"], capa["status"], capa["version"]])
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    add_event(db, capa["id"], user["id"], kind, capa["status"], to_status, note, data)
    if notify_event:
        notify.emit_capa(db, notify_event, capa["id"], user["id"])
    db.commit()
    return []


def _can_create(db, user) -> bool:
    return "capa.create" in user_permissions(db, user) or user["role"] == "admin"


def start(db, user, capa_id) -> list[str]:
    capa, errors = _open_for(db, user, capa_id, ("open",))
    if errors:
        return errors
    if user["id"] != capa["owner_id"]:
        return ["เฉพาะเจ้าของงานเท่านั้นที่เริ่มดำเนินการได้"]
    return _transition(db, capa, {}, "started", user, "in_progress")


def complete(db, user, capa_id, note: str, expected_version: int | None = None) -> list[str]:
    capa, errors = _open_for(db, user, capa_id, OPEN_STATES)
    if errors:
        return errors
    if expected_version is not None and expected_version != capa["version"]:
        return STALE_FORM
    if user["id"] != capa["owner_id"] and not _can_create(db, user):
        return ["เฉพาะเจ้าของงานหรือผู้มีสิทธิ์สร้าง CAPA เท่านั้นที่บันทึกว่าเสร็จได้"]
    note = (note or "").strip()
    if not note:
        return ["กรุณาระบุผลการดำเนินการ"]
    if len(note) > TEXT_MAX:
        return [f"ผลการดำเนินการยาวเกิน {TEXT_MAX} ตัวอักษร"]
    return _transition(db, capa, {"done_by": user["id"], "done_at": timeutil.now().strftime("%Y-%m-%d %H:%M:%S"), "done_note": note},
                       "completed", user, "done", note, notify_event="capa_done")


def _verifier_errors(db, user, capa) -> list[str]:
    if "capa.verify" not in user_permissions(db, user):
        return ["คุณไม่มีสิทธิ์ยืนยันผล CAPA"]
    if user["id"] in owners_ever(db, capa):
        return ["ผู้ที่เคยเป็นเจ้าของงานนี้ยืนยันผลเองไม่ได้ ต้องให้ผู้อื่นที่มีสิทธิ์เป็นผู้ยืนยัน"]
    if capa["done_by"] == user["id"]:
        return ["ผู้ยืนยันต้องไม่ใช่ผู้ที่กดว่าเสร็จ — ให้ผู้อื่นที่มีสิทธิ์เป็นผู้ยืนยัน"]
    return []


def verify(db, user, capa_id, note: str, effectiveness_due: str = "", expected_version: int | None = None) -> list[str]:
    capa, errors = _open_for(db, user, capa_id, ("done",))
    if errors:
        return errors
    if expected_version is not None and expected_version != capa["version"]:
        return STALE_FORM
    errors = _verifier_errors(db, user, capa)
    if errors:
        return errors
    note = (note or "").strip()
    if len(note) > 1000:
        return ["หมายเหตุการยืนยันยาวเกิน 1000 ตัวอักษร"]
    eff, derr = _date(effectiveness_due, allow_blank=True)
    if derr:
        return ["วันตรวจประสิทธิผล: " + derr]
    return _transition(db, capa, {"verified_by": user["id"], "verified_at": timeutil.now().strftime("%Y-%m-%d %H:%M:%S"), "verify_note": note,
                                  "effectiveness_due": eff.isoformat() if eff else None}, "verified", user, "verified", note, notify_event="capa_verified")


def reopen(db, user, capa_id, reason: str, expected_version: int | None = None) -> list[str]:
    capa, errors = _open_for(db, user, capa_id, ("done",))
    if errors:
        return errors
    if expected_version is not None and expected_version != capa["version"]:
        return STALE_FORM
    errors = _verifier_errors(db, user, capa)
    if errors:
        return errors
    reason = (reason or "").strip()
    if not reason:
        return ["กรุณาระบุเหตุผลที่ตีกลับ"]
    if len(reason) > NOTE_MAX:
        return [f"เหตุผลยาวเกิน {NOTE_MAX} ตัวอักษร"]
    return _transition(db, capa, {"reopen_count": capa["reopen_count"] + 1}, "reopened", user, "in_progress", reason, notify_event="capa_reopened")


def cancel(db, user, capa_id, reason: str) -> list[str]:
    capa, errors = _open_for(db, user, capa_id, OPEN_STATES)
    if errors:
        return errors
    if user["id"] != capa["created_by"] and not _can_create(db, user):
        return ["คุณไม่มีสิทธิ์ยกเลิก CAPA"]
    reason = (reason or "").strip()
    if not reason:
        return ["กรุณาระบุเหตุผลที่ยกเลิก"]
    if len(reason) > NOTE_MAX:
        return [f"เหตุผลยาวเกิน {NOTE_MAX} ตัวอักษร"]
    return _transition(db, capa, {"cancel_reason": reason}, "cancelled", user, "cancelled", reason)


def reassign(db, user, capa_id, owner_id, due_date: str = "") -> list[str]:
    capa, errors = _open_for(db, user, capa_id, OPEN_STATES)
    if errors:
        return errors
    if not _can_create(db, user):
        return ["คุณไม่มีสิทธิ์มอบหมาย CAPA"]
    owner = _int(owner_id)
    row = db.execute("SELECT * FROM safety_users WHERE id=? AND active=1", (owner,)).fetchone() if owner is not None else None
    if row is None:
        return ["กรุณาเลือกเจ้าของงานที่ใช้งานอยู่"]
    if not user_in_scope(db, row, capa["risk_area_id"]):
        return ["เจ้าของงานที่เลือกอยู่นอกขอบเขตของพื้นที่นี้"]
    due, derr = _date(due_date, allow_blank=True)
    if derr:
        return ["วันครบกำหนด: " + derr]
    due_text = due.isoformat() if due else capa["due_date"]
    return _transition(db, capa, {"owner_id": owner, "due_date": due_text}, "reassigned", user, capa["status"],
                       data={"old_owner": capa["owner_id"], "owner": owner, "due_date": due_text}, notify_event="capa_assigned")


def record_effectiveness(db, user, capa_id, result: str, note: str) -> list[str]:
    capa, errors = _open_for(db, user, capa_id, ("verified",))
    if errors:
        return errors
    if "capa.verify" not in user_permissions(db, user):
        return ["คุณไม่มีสิทธิ์บันทึกผลการตรวจประสิทธิผล"]
    if user["id"] in owners_ever(db, capa):
        return ["ผู้ที่เคยเป็นเจ้าของงานนี้บันทึกผลการตรวจประสิทธิผลเองไม่ได้"]
    if not capa["effectiveness_due"]:
        return ["CAPA นี้ไม่ได้กำหนดวันตรวจประสิทธิผล"]
    if capa["effectiveness_result"]:
        return ["บันทึกผลการตรวจประสิทธิผลไปแล้ว แก้ไขไม่ได้"]
    if capa["effectiveness_due"] > timeutil.today().isoformat():
        return [f"ยังไม่ถึงกำหนดตรวจประสิทธิผล ({capa['effectiveness_due']})"]
    if result not in ("effective", "ineffective"):
        return ["ผลการตรวจประสิทธิผลต้องเป็นได้ผลหรือไม่ได้ผล"]
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        return [f"หมายเหตุยาวเกิน {NOTE_MAX} ตัวอักษร"]
    cur = db.execute("UPDATE capas SET effectiveness_result=?, effectiveness_note=?, version=version+1, updated_at=datetime('now','localtime') "
                     "WHERE id=? AND status='verified' AND version=? AND effectiveness_result IS NULL", (result, note, capa["id"], capa["version"]))
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    add_event(db, capa["id"], user["id"], "effectiveness", "verified", "verified", note, {"result": result})
    db.commit()
    return []


# ---------- ประตูตรวจ: ปิดรายการต้นทางได้เมื่อ CAPA ยืนยันครบ ----------
def gate_enabled(db) -> bool:
    row = db.execute("SELECT value FROM app_settings WHERE key='require_capa_verified'").fetchone()
    return bool(row) and row[0] == "1"


def open_blockers(db, source_kind: str, source_id: int) -> int:
    """จำนวน CAPA ของรายการต้นทางนี้ที่ยังไม่ยืนยัน (ไม่นับที่ยกเลิก); รายการอันตรายที่สร้างจากข้อไม่ผ่านของการตรวจ
    นับ CAPA ของข้อนั้น (source=patrol) ด้วย เพราะเป็นข้อค้นพบเดียวกัน"""
    sql = "SELECT COUNT(*) FROM capas WHERE status NOT IN ('verified','cancelled') AND ((source_kind=? AND source_id=?)"
    args: list = [source_kind, source_id]
    if source_kind == "hazard":
        row = db.execute("SELECT patrol_answer_id FROM hazards WHERE id=?", (source_id,)).fetchone()
        if row and row[0] is not None:
            sql += " OR (source_kind='patrol' AND source_id=?)"
            args.append(row[0])
    return db.execute(sql + ")", args).fetchone()[0]


def gate_errors(db, source_kind: str, source_id: int) -> list[str]:
    if not gate_enabled(db):
        return []
    n = open_blockers(db, source_kind, source_id)
    return [f"ยังมี CAPA ที่ยังไม่ยืนยันผล {n} รายการ — ต้องยืนยันครบก่อนปิดรายการนี้"] if n else []


# ---------- รายการ/สรุป ----------
def _visible_where(db, user) -> tuple[str, list]:
    sql, args = scope_sql(user)
    if not user_permissions(db, user) & {"capa.create", "capa.verify"}:
        sql, args = f"{sql} AND (c.owner_id = ? OR c.created_by = ?)", args + [user["id"]] * 2
    return sql, args


def overdue_count(db, user, today: date | None = None) -> int:
    where, args = _visible_where(db, user)
    return db.execute(f"SELECT COUNT(*) FROM capas c WHERE {where} AND c.status IN ('open','in_progress') AND c.due_date < ?",
                      args + [(today or timeutil.today()).isoformat()]).fetchone()[0]


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_capas(db, user, *, status: str = "", mine: bool = False, overdue: bool = False, q: str = "", limit: int = 20, offset: int = 0):
    """(แถว, จำนวนทั้งหมด) ตามขอบเขต/สิทธิ์การมองเห็น + ตัวกรอง"""
    where, args = _visible_where(db, user)
    if status in STATUS_TH:
        where, args = where + " AND c.status = ?", args + [status]
    if mine:
        where, args = where + " AND (c.owner_id = ? OR c.created_by = ?)", args + [user["id"]] * 2
    if overdue:
        where, args = where + " AND c.status IN ('open','in_progress') AND c.due_date < ?", args + [timeutil.today().isoformat()]
    q = (q or "").strip()[:100]
    if q:
        where, args = where + " AND (c.code LIKE ? ESCAPE '\\' OR c.title LIKE ? ESCAPE '\\')", args + [_like(q)] * 2
    total = db.execute(f"SELECT COUNT(*) FROM capas c WHERE {where}", args).fetchone()[0]
    rows = db.execute(f"SELECT c.*, a.name_th AS area_name, u.name AS owner_name FROM capas c JOIN risk_areas a ON a.id = c.risk_area_id "
                      f"JOIN safety_users u ON u.id = c.owner_id WHERE {where} ORDER BY c.due_date, c.id LIMIT ? OFFSET ?",
                      args + [limit, offset]).fetchall()
    return rows, total


def for_source(db, user, source_kind: str, source_id: int) -> list:
    """CAPA ของรายการต้นทางที่ผู้ใช้มองเห็น"""
    return [r for r in db.execute("SELECT * FROM capas WHERE source_kind=? AND source_id=? ORDER BY id", (source_kind, source_id))
            if visible_capa(db, user, r["id"]) is not None]
