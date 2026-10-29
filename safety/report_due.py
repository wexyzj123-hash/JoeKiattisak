"""safety/report_due.py — กำหนดส่งรายงาน (report_due) และบันทึก "ส่งรายงานแล้ว" (report_submissions: เพิ่มได้อย่างเดียว ห้ามแก้/ลบ)
งวดรายเดือน: ส่งรายงานของเดือนก่อนหน้าในวันที่ due_day ของเดือนถัดไป; งวดรายปี: ส่งรายงานของปีก่อนในวันที่ due_day/due_month ของปีถัดไป
กำหนดส่งที่ผ่านไปก่อนวันที่สร้างกำหนดไม่ถูกนับเป็นค้างส่ง (ไม่เตือนย้อนหลัง)"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from . import reports, timeutil
from .patrol import add_months
from .perms import user_permissions

DENIED = ["คุณไม่มีสิทธิ์ดำเนินการนี้"]
NOT_FOUND = ["ไม่พบกำหนดส่งรายงาน"]
FREQUENCY_TH = {"monthly": "รายเดือน", "yearly": "รายปี"}
PERIOD_RE = re.compile(r"^(\d{4})(?:-(0[1-9]|1[0-2]))?$")
NOTE_MAX = 500


FREQUENCY_FOR = {"monthly": "monthly", "capa": "monthly", "annual": "yearly", "incidents": "yearly"}      # ความถี่ที่รายงานแต่ละแบบใช้ (กำหนดรูปแบบงวด)


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _has(db, user, *keys) -> bool:
    return user["role"] == "admin" or bool(set(keys) & user_permissions(db, user))


def _unscoped(user) -> bool:
    return user["factory_id"] is None and user["department_id"] is None


def _due_date(rule, year: int, month: int) -> date:
    return add_months(date(year, month, 1), 0).replace(day=min(rule["due_day"], 28))


HISTORY = {"monthly": 12, "yearly": 3}                              # ย้อนดูงวดที่ค้างส่งได้ไกลสุดกี่งวด


def _dues(rule, today: date, back: int = 1) -> list[tuple[str, date]]:
    """(งวด, วันครบกำหนด) รอบ ๆ วันนี้ (ย้อนหลัง back งวด ไปข้างหน้า 1 งวด) เรียงตามวันครบกำหนด"""
    out = []
    if rule["frequency"] == "monthly":
        for k in range(-back, 2):
            first = add_months(date(today.year, today.month, 1), k)
            prev = add_months(first, -1)
            out.append((f"{prev.year:04d}-{prev.month:02d}", _due_date(rule, first.year, first.month)))
    else:
        for k in range(-back, 2):
            y = today.year + k
            out.append((f"{y - 1:04d}", _due_date(rule, y, rule["due_month"] or 1)))
    return sorted(out, key=lambda x: x[1])


def period_due(rule, today: date | None = None) -> tuple[str, date]:
    """งวดและวันครบกำหนดถัดไป (วันนี้หรือหลังจากนี้)"""
    today = today or timeutil.today()
    return next(d for d in _dues(rule, today) if d[1] >= today)


def _submitted(db, key: str, dept, period: str) -> bool:
    return db.execute("SELECT 1 FROM report_submissions WHERE report_key=? AND COALESCE(department_id,0)=COALESCE(?,0) AND period=?",
                      (key, dept, period)).fetchone() is not None


def status(db, rule, today: date | None = None) -> dict:
    """state: overdue (เลยกำหนดและยังไม่ส่ง) | due (ครบกำหนดวันนี้) | upcoming (งวดถัดไป, days = จำนวนวันที่เหลือ)"""
    today = today or timeutil.today()
    created = (rule["created_at"] or "")[:10] or "0001-01-01"
    dues = _dues(rule, today, HISTORY[rule["frequency"]])
    past = [d for d in dues if d[1] <= today and d[1].isoformat() >= created]
    missing = [d for d in past if not _submitted(db, rule["report_key"], rule["department_id"], d[0])]
    if missing:                                                    # งวดที่ค้างเก่าสุดก่อน — ไม่หายไปเมื่อถึงกำหนดของงวดถัดไป
        period, due = missing[0]
        return {"state": "due" if due == today else "overdue", "period": period, "due": due, "days": abs((today - due).days), "submitted": False}
    period, due = next(d for d in dues if d[1] > today)
    return {"state": "upcoming", "period": period, "due": due, "days": (due - today).days, "submitted": _submitted(db, rule["report_key"], rule["department_id"], period)}


def _dept_ok(db, user, dept) -> list[str]:
    if dept is None:
        return [] if _unscoped(user) else ["ผู้ที่มีขอบเขตเฉพาะโรงงาน/แผนกต้องเลือกแผนกของตน"]
    return [] if reports.department_in_scope(db, user, dept) is not None else NOT_FOUND


def mark_submitted(db, user, report_key: str, department_id, period: str, note: str) -> list[str]:
    if not _has(db, user, "report.export", "report.manage"):
        return DENIED
    if report_key not in reports.REPORTS:
        return ["ไม่พบรายงานนี้"]
    m = PERIOD_RE.match((period or "").strip())
    if m is None or (m.group(2) is not None) != (FREQUENCY_FOR[report_key] == "monthly"):
        return ["งวดรายงานไม่ถูกต้อง"]
    today = timeutil.today()
    year = int(m.group(1))
    elapsed = (year, int(m.group(2))) < (today.year, today.month) if m.group(2) else year < today.year
    if not elapsed:
        return ["ยังไม่ถึงงวดรายงานนี้"]                         # รับเฉพาะงวดที่จบครบแล้ว
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        return [f"หมายเหตุยาวเกิน {NOTE_MAX} ตัวอักษร"]
    dept = _int(department_id)
    errors = _dept_ok(db, user, dept)
    if errors:
        return errors
    try:
        db.execute("INSERT INTO report_submissions(report_key, department_id, period, submitted_by, note) VALUES(?,?,?,?,?)",
                   (report_key, dept, period.strip(), user["id"], note))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return ["ส่งรายงานงวดนี้แล้ว"]
    return []


def _rule_visible(db, user, rule) -> bool:
    return _unscoped(user) if rule["department_id"] is None else reports.department_in_scope(db, user, rule["department_id"]) is not None


def rules(db, user) -> list:
    return [r for r in db.execute("SELECT * FROM report_due ORDER BY active DESC, report_key, department_id, id") if _rule_visible(db, user, r)]


def submissions(db, user, limit: int = 30) -> list:
    rows = db.execute("SELECT s.*, u.name AS who, d.name_th AS dept_name FROM report_submissions s JOIN safety_users u ON u.id = s.submitted_by "
                      "LEFT JOIN departments d ON d.id = s.department_id ORDER BY s.id DESC").fetchall()
    return [r for r in rows if _rule_visible(db, user, r)][:limit]


def save_rule(db, user, form) -> list[str]:
    if not _has(db, user, "report.manage"):
        return DENIED
    rid = _int(form.get("id"))
    existing = None
    if rid is not None:
        existing = db.execute("SELECT * FROM report_due WHERE id=?", (rid,)).fetchone()
        if existing is None or not _rule_visible(db, user, existing):
            return NOT_FOUND
    key = existing["report_key"] if existing else (form.get("report_key") or "").strip()
    dept = existing["department_id"] if existing else _int(form.get("department_id"))
    freq, day, month = (form.get("frequency") or "").strip(), _int(form.get("due_day")), _int(form.get("due_month"))
    if key not in reports.REPORTS:
        return ["ไม่พบรายงานนี้"]
    if freq not in FREQUENCY_TH:
        return ["ความถี่ต้องเป็นรายเดือนหรือรายปี"]
    if freq != FREQUENCY_FOR[key]:
        return [f"รายงานนี้ใช้ความถี่{FREQUENCY_TH[FREQUENCY_FOR[key]]}เท่านั้น"]
    if day is None or not 1 <= day <= 28:
        return ["วันที่ครบกำหนดต้องอยู่ระหว่าง 1–28"]
    if freq == "yearly" and (month is None or not 1 <= month <= 12):
        return ["กำหนดส่งรายปีต้องระบุเดือน"]
    month = month if freq == "yearly" else None
    if existing is None:
        errors = _dept_ok(db, user, dept)
        if errors:
            return errors
    try:
        if existing:
            db.execute("UPDATE report_due SET frequency=?, due_day=?, due_month=? WHERE id=?", (freq, day, month, rid))
        else:
            db.execute("INSERT INTO report_due(report_key, department_id, frequency, due_day, due_month, created_by) VALUES(?,?,?,?,?,?)",
                       (key, dept, freq, day, month, user["id"]))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return ["มีกำหนดส่งของรายงานนี้ในแผนกนี้ที่ใช้งานอยู่แล้ว"]
    return []


def toggle_rule(db, user, rule_id: int) -> list[str]:
    if not _has(db, user, "report.manage"):
        return DENIED
    rule = db.execute("SELECT * FROM report_due WHERE id=?", (rule_id,)).fetchone()
    if rule is None or not _rule_visible(db, user, rule):
        return NOT_FOUND
    try:
        if rule["active"]:
            db.execute("UPDATE report_due SET active = 0 WHERE id=?", (rule_id,))
        else:                                                        # เปิดใหม่ = เริ่มนับใหม่ ไม่ถือว่าค้างส่งย้อนหลังตอนปิดอยู่
            db.execute("UPDATE report_due SET active = 1, created_at = datetime('now','localtime') WHERE id=?", (rule_id,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return ["มีกำหนดส่งของรายงานนี้ในแผนกนี้ที่ใช้งานอยู่แล้ว — ปิดกำหนดนั้นก่อน"]
    return []
