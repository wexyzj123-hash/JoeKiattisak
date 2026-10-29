"""shared/timezone.py — โซนเวลาของระบบ: เวลาไทยเป็นค่าเริ่มต้นแม้เครื่องเซิร์ฟเวอร์ตั้งเป็น UTC"""
from __future__ import annotations

import os
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Bangkok"


def apply_timezone() -> str:
    """ตั้งโซนเวลาของโปรเซสทั้งหมด (datetime.now, SQLite 'localtime', log) เป็นเวลาไทย (หรือ SAFETY_TIMEZONE) ไม่ว่าเครื่องจะตั้งเป็น UTC
    ชื่อโซนที่ไม่รู้จักถอยกลับเป็น Asia/Bangkok; คืนชื่อโซนที่ใช้จริง"""
    name = (os.environ.get("SAFETY_TIMEZONE") or DEFAULT_TZ).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        name = DEFAULT_TZ
    os.environ["TZ"] = name
    if hasattr(time, "tzset"):                      # Windows ไม่มี time.tzset — ใช้โซนเวลาของเครื่อง (ตั้ง Windows เป็นเวลาไทย)
        time.tzset()
    return name
