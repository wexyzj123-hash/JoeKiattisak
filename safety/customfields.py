"""safety/customfields.py — ฟิลด์เพิ่มเติมต่อประเภทอันตราย (กำหนดจากหน้าจอ ไม่ต้องแก้โค้ด) และการตรวจค่าที่กรอก"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Mapping

TYPES = {"text": "ข้อความ", "number": "ตัวเลข", "select": "ตัวเลือก", "date": "วันที่", "checkbox": "ช่องทำเครื่องหมาย"}
TEXT_MAX = 500
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fields_for_type(db, hazard_type_id: int | None, include_inactive: bool = False) -> list:
    """ฟิลด์เฉพาะประเภทนั้น + ฟิลด์ของทุกประเภท เรียงตามลำดับ"""
    where, args = "hazard_type_id IS NULL", []
    if hazard_type_id is not None:
        where, args = "(hazard_type_id IS NULL OR hazard_type_id = ?)", [hazard_type_id]
    if not include_inactive:
        where += " AND active = 1"
    return db.execute(f"SELECT * FROM custom_fields WHERE {where} ORDER BY sort, id", args).fetchall()


def split_options(options: str) -> list[str]:
    return [o.strip() for o in (options or "").splitlines() if o.strip()]


def _posted(form: Mapping, name: str) -> str:
    """ค่าแรกที่ไม่ว่างของฟิลด์ชื่อนี้ (เบราว์เซอร์ที่ไม่มี JS อาจส่งช่องของประเภทอื่นที่ซ่อนอยู่มาก่อนด้วยค่าว่าง)"""
    values = form.getlist(name) if hasattr(form, "getlist") else [form.get(name)]
    return next((v.strip() for v in values if v and v.strip()), "")


def validate_custom(fields, form: Mapping) -> tuple[dict, list[str]]:
    """อ่านค่า cf_<key> จากฟอร์ม ตรวจตามชนิด; คืน ({key: ค่า}, ข้อความผิดพลาด) — ฟิลด์ที่ไม่บังคับและเว้นว่างจะไม่ถูกเก็บ"""
    values: dict = {}
    errors: list[str] = []
    for f in fields:
        key, label, kind = f["key"], f["label_th"], f["type"]
        raw = _posted(form, f"cf_{key}")
        if kind == "checkbox":
            ticked = raw.lower() in ("1", "on", "true")
            if f["required"] and not ticked:
                errors.append(f"กรุณาทำเครื่องหมาย {label}")
            values[key] = ticked
            continue
        if not raw:
            if f["required"]:
                errors.append(f"กรุณากรอก {label}")
            continue
        if kind == "text":
            if len(raw) > TEXT_MAX:
                errors.append(f"{label} ยาวเกิน {TEXT_MAX} ตัวอักษร")
            else:
                values[key] = raw
        elif kind == "number":
            try:
                num = float(raw)
            except ValueError:
                num = math.nan
            if not math.isfinite(num):
                errors.append(f"{label} ต้องเป็นตัวเลข")
            else:
                values[key] = int(num) if num.is_integer() and abs(num) < 1e15 else num
        elif kind == "date":
            try:
                if not DATE_RE.match(raw):
                    raise ValueError
                date.fromisoformat(raw)
                values[key] = raw
            except ValueError:
                errors.append(f"{label} ต้องเป็นวันที่รูปแบบ ปปปป-ดด-วว ที่มีอยู่จริง")
        elif kind == "select":
            if raw in split_options(f["options"]):
                values[key] = raw
            else:
                errors.append(f"{label}: ตัวเลือกไม่ถูกต้อง")
    return values, errors
