"""safety/timeutil.py — วันที่ปัจจุบันในจุดเดียว (ทดสอบจำลองวันได้ด้วย monkeypatch)"""
from __future__ import annotations

from datetime import date


def today() -> date:
    return date.today()
