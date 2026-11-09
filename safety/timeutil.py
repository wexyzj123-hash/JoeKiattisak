"""safety/timeutil.py — วันที่ปัจจุบันในจุดเดียว (ทดสอบจำลองวันได้ด้วย monkeypatch)"""
from __future__ import annotations

from datetime import date, datetime

from shared.timezone import apply_timezone  # noqa: F401  (ใช้ซ้ำกับ Hub)


def today() -> date:
    return date.today()


def now() -> datetime:
    return datetime.now()
