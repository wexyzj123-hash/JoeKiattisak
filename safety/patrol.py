"""safety/patrol.py — Safety Patrol: แผนการตรวจ (ครบกำหนด/เกินกำหนด), รอบตรวจ, ข้อไม่ผ่าน
รอบตรวจที่ส่งแล้วแก้ไม่ได้ (ทริกเกอร์ในฐานข้อมูลกันไว้อีกชั้น)"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import timeutil

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
