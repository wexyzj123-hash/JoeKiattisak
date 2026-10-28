"""safety/patrol.py — Safety Patrol: แผนการตรวจ (ครบกำหนด/เกินกำหนด), รอบตรวจ, ข้อไม่ผ่าน
รอบตรวจที่ส่งแล้วแก้ไม่ได้ (ทริกเกอร์ในฐานข้อมูลกันไว้อีกชั้น)"""
from __future__ import annotations

import calendar
import os
import re
import sqlite3
from datetime import date, timedelta
from typing import Mapping

from . import timeutil
from .hazards import hazard_scope_sql, risk_area_options
from .images import ImageError, save_attachment
from .incidents import LOST_RACE, NOT_FOUND
from .perms import user_permissions

STALE_FORM = ["ข้อมูลรอบตรวจนี้ถูกแก้ไขโดยผู้อื่นแล้ว กรุณารีเฟรชหน้าแล้วทำรายการใหม่"]
NOTE_MAX = 500
FIELD_RE = re.compile(r"^(result|note)_(\d+)$")
RESULTS = ("pass", "fail", "na")

PERIOD_DAYS = {"daily": 1, "weekly": 7}


def add_months(day: date, months: int) -> date:
    """บวกเดือนปฏิทิน วันที่เกินสิ้นเดือนถอยเป็นวันสุดท้ายของเดือน (31 ม.ค. → 28/29 ก.พ.)"""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def plan_status(db, plan, today: date | None = None) -> dict:
    """สถานะของแผนการตรวจ: due = วันที่ครบกำหนดรอบถัดไป (จากรอบที่ส่งแล้วล่าสุดของแบบตรวจ+พื้นที่นี้ หรือวันสร้างแผนถ้ายังไม่เคยตรวจ),
    state = ok | due (วันนี้ครบ) | overdue; พลาดหลายรอบนับเป็นสถานะเดียว (days = จำนวนวันที่เลยมา)"""
    today = today or timeutil.today()
    row = db.execute("SELECT MAX(substr(submitted_at, 1, 10)) FROM patrol_rounds WHERE checklist_id=? AND risk_area_id=? AND status='submitted'",
                     (plan["checklist_id"], plan["risk_area_id"])).fetchone()
    last = date.fromisoformat(row[0]) if row and row[0] else None
    if last is None:
        due = date.fromisoformat(str(plan["created_at"])[:10])
    elif plan["frequency"] == "monthly":
        due = add_months(last, 1)
    else:
        due = last + timedelta(days=PERIOD_DAYS[plan["frequency"]])
    delta = (today - due).days
    return {"due": due, "last": last, "state": "ok" if delta < 0 else ("due" if delta == 0 else "overdue"),
            "days": -delta if delta < 0 else delta}


# ---------- รอบตรวจ ----------
def _scope(user, alias: str) -> tuple[str, list]:
    sql, args = hazard_scope_sql(user)
    return sql.replace("h.risk_area_id", f"{alias}.risk_area_id"), list(args)


def _is_editor(user, round_) -> bool:
    return user["id"] == round_["inspector_id"] or user["role"] == "admin"


def visible_round(db, user, round_id: int):
    """รอบตรวจที่ผู้ใช้มองเห็น: ร่างเห็นเฉพาะผู้ตรวจเจ้าของ/แอดมิน; ที่ส่งแล้วเห็นตามขอบเขต (patrol.view = ทุกรอบ, patrol.perform = ของตัวเอง)"""
    sql, args = _scope(user, "r")
    row = db.execute(f"SELECT r.* FROM patrol_rounds r WHERE r.id=? AND {sql}", [round_id] + args).fetchone()
    if row is None:
        return None
    if row["status"] == "draft":
        return row if _is_editor(user, row) else None
    perms = user_permissions(db, user)
    if "patrol.view" in perms or (_is_editor(user, row) and "patrol.perform" in perms):
        return row
    return None


def _visible_where(db, user) -> tuple[str, list]:
    sql, args = _scope(user, "r")
    perms = user_permissions(db, user)
    if "patrol.view" in perms:
        return f"{sql} AND (r.status='submitted' OR r.inspector_id=?)", args + [user["id"]]
    if "patrol.perform" in perms:
        return f"{sql} AND r.inspector_id=?", args + [user["id"]]
    return "0=1", []


_ROUND_COLS = ("SELECT r.*, a.name_th AS area_name, c.name_th AS checklist_name, u.name AS inspector_name FROM patrol_rounds r "
               "JOIN risk_areas a ON a.id = r.risk_area_id JOIN checklists c ON c.id = r.checklist_id JOIN safety_users u ON u.id = r.inspector_id ")


def rounds_for(db, user, limit: int = 20, offset: int = 0, status: str | None = None) -> list:
    where, args = _visible_where(db, user)
    if status in ("draft", "submitted"):
        where, args = where + " AND r.status=?", args + [status]
    return db.execute(_ROUND_COLS + f"WHERE {where} ORDER BY COALESCE(r.submitted_at, r.started_at) DESC, r.id DESC LIMIT ? OFFSET ?",
                      args + [max(1, min(int(limit), 200)), max(0, int(offset))]).fetchall()


def latest_rounds(db, user, limit: int = 5) -> list:
    """รอบตรวจที่ส่งแล้วล่าสุดที่ผู้ใช้เห็น (ใช้กับแดชบอร์ด)"""
    return rounds_for(db, user, limit=limit, status="submitted")


def answers_for(db, round_id: int) -> list:
    return db.execute("SELECT a.*, (SELECT COUNT(*) FROM patrol_attachments p WHERE p.answer_id = a.id) AS photos FROM patrol_answers a "
                      "WHERE a.round_id=? ORDER BY a.sort, a.id", (round_id,)).fetchall()


def due_plans(db, user, today: date | None = None) -> list[dict]:
    """แผนการตรวจที่ถึงรอบ/เลยรอบและผู้ใช้เห็น เรียงตามที่เลยมานานที่สุดก่อน"""
    perms = user_permissions(db, user)
    if not perms & {"patrol.perform", "patrol.view"}:
        return []
    sql, args = _scope(user, "p")
    plans = db.execute(f"SELECT p.*, c.name_th AS checklist_name, a.name_th AS area_name FROM patrol_plans p JOIN checklists c ON c.id = p.checklist_id "
                       f"JOIN risk_areas a ON a.id = p.risk_area_id WHERE p.active=1 AND c.active=1 AND {sql} ORDER BY p.id", args).fetchall()
    out = []
    for plan in plans:
        st = plan_status(db, plan, today)
        if st["state"] != "ok":
            out.append({**dict(plan), "status": st})
    out.sort(key=lambda p: (-p["status"]["days"], p["id"]))
    return out


def _next_code(db, today: date) -> str:
    prefix = f"PT-{today:%y%m}-"
    row = db.execute("SELECT code FROM patrol_rounds WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (prefix + "%",)).fetchone()
    try:
        seq = int(row["code"][len(prefix):]) + 1 if row else 1
    except ValueError:
        seq = 1
    return f"{prefix}{seq:04d}"


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def start_round(db, user, plan_id=None, checklist_id=None, risk_area_id=None) -> tuple[int | None, list[str]]:
    if "patrol.perform" not in user_permissions(db, user):
        return None, ["คุณไม่มีสิทธิ์ตรวจความปลอดภัย"]
    plan = None
    if plan_id is not None:
        sql, args = _scope(user, "p")
        plan = db.execute(f"SELECT p.* FROM patrol_plans p WHERE p.id=? AND p.active=1 AND {sql}", [_int(plan_id)] + args).fetchone()
        if plan is None:
            return None, ["ไม่พบแผนการตรวจ"]
        checklist_id, risk_area_id = plan["checklist_id"], plan["risk_area_id"]
    cid, area = _int(checklist_id), _int(risk_area_id)
    if area is None or area not in {o["id"] for o in risk_area_options(db, user)}:
        return None, ["กรุณาเลือกพื้นที่เสี่ยงที่ใช้งานอยู่และอยู่ในขอบเขตของคุณ"]
    if cid is None or db.execute("SELECT 1 FROM checklists WHERE id=? AND active=1", (cid,)).fetchone() is None:
        return None, ["กรุณาเลือกแบบตรวจที่ใช้งานอยู่"]
    items = db.execute("SELECT * FROM checklist_items WHERE checklist_id=? AND active=1 ORDER BY sort, id", (cid,)).fetchall()
    if not items:
        return None, ["แบบตรวจนี้ยังไม่มีรายการตรวจที่ใช้งานอยู่"]
    existing = db.execute("SELECT id FROM patrol_rounds WHERE inspector_id=? AND checklist_id=? AND risk_area_id=? AND status='draft'",
                          (user["id"], cid, area)).fetchone()
    if existing:
        return existing["id"], []
    if plan is None:
        plan = db.execute("SELECT id FROM patrol_plans WHERE checklist_id=? AND risk_area_id=? AND active=1", (cid, area)).fetchone()
    for attempt in (1, 2):
        try:
            cur = db.execute("INSERT INTO patrol_rounds(code, plan_id, checklist_id, risk_area_id, inspector_id) VALUES(?,?,?,?,?)",
                             (_next_code(db, timeutil.today()), plan["id"] if plan else None, cid, area, user["id"]))
            break
        except sqlite3.IntegrityError:
            db.rollback()
            if attempt == 2:
                return None, ["สร้างรหัสรอบตรวจไม่สำเร็จ กรุณาลองอีกครั้ง"]
    db.executemany("INSERT INTO patrol_answers(round_id, item_id, section, text_th, weight, require_photo, sort) VALUES(?,?,?,?,?,?,?)",
                   [(cur.lastrowid, i["id"], i["section"], i["text_th"], i["weight"], i["require_photo_on_fail"], n) for n, i in enumerate(items)])
    db.commit()
    return cur.lastrowid, []


def _editable(db, user, round_id: int):
    """ร่างที่ผู้ใช้แก้ได้ (ผู้ตรวจเจ้าของ/แอดมิน) → (round, errors)"""
    row = visible_round(db, user, round_id)
    if row is None:
        return None, NOT_FOUND
    if row["status"] != "draft":
        return None, ["รอบตรวจนี้ส่งแล้ว แก้ไขไม่ได้"]
    return row, []


def save_answers(db, user, round_id: int, form: Mapping, expected_version: int | None = None) -> list[str]:
    rnd, errors = _editable(db, user, round_id)
    if errors:
        return errors
    if expected_version is not None and expected_version != rnd["version"]:
        return STALE_FORM
    owned = {a["id"] for a in db.execute("SELECT id FROM patrol_answers WHERE round_id=?", (round_id,))}
    updates: dict[int, dict] = {}
    for key in form:
        m = FIELD_RE.match(key)
        if not m:
            continue
        kind, aid = m.group(1), int(m.group(2))
        if aid not in owned:
            return ["ข้อมูลไม่ตรงกับรอบตรวจนี้"]
        value = (form.get(key) or "").strip()
        if kind == "result" and value not in ("",) + RESULTS:
            return ["ผลการตรวจต้องเป็น ผ่าน ไม่ผ่าน หรือไม่เกี่ยวข้อง"]
        if kind == "note" and len(value) > NOTE_MAX:
            return [f"หมายเหตุยาวเกิน {NOTE_MAX} ตัวอักษร"]
        updates.setdefault(aid, {})[kind] = value
    cur = db.execute("UPDATE patrol_rounds SET version=version+1 WHERE id=? AND status='draft' AND version=?", (round_id, rnd["version"]))
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    for aid, f in updates.items():
        if "result" in f:
            db.execute("UPDATE patrol_answers SET result=? WHERE id=?", (f["result"] or None, aid))
        if "note" in f:
            db.execute("UPDATE patrol_answers SET note=? WHERE id=?", (f["note"], aid))
    db.commit()
    return []


def add_photo(db, user, round_id: int, answer_id: int, filename: str, data: bytes, upload_root: str) -> list[str]:
    rnd, errors = _editable(db, user, round_id)
    if errors:
        return errors
    if db.execute("SELECT 1 FROM patrol_answers WHERE id=? AND round_id=?", (_int(answer_id), round_id)).fetchone() is None:
        return ["ข้อมูลไม่ตรงกับรอบตรวจนี้"]
    try:
        save_attachment(db, upload_root, int(answer_id), "before", filename, data, user["id"], scope="patrol")
    except ImageError as exc:
        return [f"{filename}: {exc}"]
    return []


def delete_draft(db, user, round_id: int, upload_root: str) -> list[str]:
    rnd, errors = _editable(db, user, round_id)
    if errors:
        return errors
    files = db.execute("SELECT p.answer_id, p.stored_name FROM patrol_attachments p JOIN patrol_answers a ON a.id = p.answer_id WHERE a.round_id=?",
                       (round_id,)).fetchall()
    db.execute("DELETE FROM patrol_attachments WHERE answer_id IN (SELECT id FROM patrol_answers WHERE round_id=?)", (round_id,))
    db.execute("DELETE FROM patrol_answers WHERE round_id=?", (round_id,))
    if db.execute("DELETE FROM patrol_rounds WHERE id=? AND status='draft'", (round_id,)).rowcount != 1:
        db.rollback()
        return LOST_RACE
    db.commit()
    for f in files:                                                    # ลบไฟล์หลังบันทึกฐานข้อมูลสำเร็จ (ล้มเหลวก็ไม่กระทบข้อมูล)
        try:
            os.remove(os.path.join(upload_root, f"patrol-{f['answer_id']}", f["stored_name"]))
        except OSError:
            pass
    return []


def submit_round(db, user, round_id: int, expected_version: int | None = None) -> list[str]:
    rnd, errors = _editable(db, user, round_id)
    if errors:
        return errors
    if expected_version is not None and expected_version != rnd["version"]:
        return STALE_FORM
    rows = answers_for(db, round_id)
    missing = [a for a in rows if a["result"] is None]
    if missing:
        return [f"กรุณาตอบให้ครบทุกข้อ (ยังไม่ได้ตอบ {len(missing)} ข้อ)"]
    lacking = [a for a in rows if a["result"] == "fail" and a["require_photo"] and a["photos"] == 0]
    if lacking:
        return [f"ข้อที่ไม่ผ่านต้องแนบรูปอย่างน้อย 1 รูป (ยังขาด {len(lacking)} ข้อ)"]
    passed, failed, na = (sum(1 for a in rows if a["result"] == k) for k in RESULTS)
    pw, fw = (sum(a["weight"] for a in rows if a["result"] == k) for k in ("pass", "fail"))
    score = round(pw * 100 / (pw + fw)) if pw + fw else None
    cur = db.execute("UPDATE patrol_rounds SET status='submitted', submitted_at=datetime('now','localtime'), passed=?, failed=?, na=?, score_pct=?, "
                     "version=version+1 WHERE id=? AND status='draft' AND version=?", (passed, failed, na, score, round_id, rnd["version"]))
    if cur.rowcount != 1:
        db.rollback()
        return LOST_RACE
    db.commit()
    return []
