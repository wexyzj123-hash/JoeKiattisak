"""safety/master.py — เครื่องมือข้อมูลหลักแบบทั่วไป: เพิ่มตารางใหม่ = เพิ่มรายการใน REGISTRY (ไม่ต้องเขียนหน้าจอใหม่)"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Mapping

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
NAME_MAX, DESC_MAX = 200, 1000


@dataclass(frozen=True)
class TableDef:
    name: str                 # ชื่อตารางในฐานข้อมูล
    slug: str                 # ส่วนของ URL
    label_th: str


@dataclass
class Page:
    rows: list
    page: int
    pages: int
    total: int
    per_page: int = 20


REGISTRY: dict[str, TableDef] = {td.slug: td for td in (
    TableDef("hazard_types", "hazard-types", "ประเภทอันตราย"),
    TableDef("hazard_sources", "hazard-sources", "แหล่งที่มา"),
)}

# คอลัมน์ที่เรียงได้ (ชื่อจาก URL → นิพจน์ SQL ที่เขียนตายตัว; ข้อความจากผู้ใช้ไม่เคยถูกต่อเข้า SQL)
SORTS = {"code": "t.code", "name_th": "t.name_th", "active": "t.active", "creator": "u.name",
         "created_at": "t.created_at"}


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_rows(db, td: TableDef, q: str = "", sort: str = "code", direction: str = "asc",
              page: int = 1, per_page: int = 20) -> Page:
    expr = SORTS.get(sort, SORTS["code"])
    order = "DESC" if direction == "desc" else "ASC"
    where, args = "", []
    if q.strip():
        where = " WHERE (t.code LIKE ? ESCAPE '\\' OR t.name_th LIKE ? ESCAPE '\\')"
        args = [_like(q.strip())] * 2
    base = f"FROM {td.name} t LEFT JOIN safety_users u ON u.id = t.created_by{where}"
    total = db.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    rows = db.execute(f"SELECT t.*, u.name AS creator {base} ORDER BY {expr} {order}, t.id "
                      f"LIMIT ? OFFSET ?", args + [per_page, (page - 1) * per_page]).fetchall()
    return Page(rows, page, pages, total, per_page)


def get_row(db, td: TableDef, row_id: int):
    return db.execute(f"SELECT * FROM {td.name} WHERE id=?", (row_id,)).fetchone()


def save_row(db, td: TableDef, row_id: int | None, form: Mapping, user_id: int) -> list[str]:
    """บันทึกแถวใหม่/แก้ไข; คืนรายการข้อความผิดพลาดภาษาไทย (ว่าง = บันทึกแล้ว)"""
    code = (form.get("code") or "").strip()
    name = (form.get("name_th") or "").strip()
    desc = (form.get("description") or "").strip()
    errors: list[str] = []
    if not CODE_RE.match(code):
        errors.append("รหัสใช้ได้เฉพาะ A-Z a-z 0-9 _ - ความยาว 1-32 ตัวอักษร")
    if not name:
        errors.append("กรุณากรอกชื่อ")
    elif len(name) > NAME_MAX:
        errors.append(f"ชื่อยาวเกิน {NAME_MAX} ตัวอักษร")
    if len(desc) > DESC_MAX:
        errors.append(f"คำอธิบายยาวเกิน {DESC_MAX} ตัวอักษร")
    if not errors and db.execute(f"SELECT 1 FROM {td.name} WHERE code = ? COLLATE NOCASE AND id <> ?",
                                 (code, row_id or 0)).fetchone():
        errors.append("รหัสนี้ถูกใช้แล้ว")
    if errors:
        return errors
    try:
        if row_id is None:
            db.execute(f"INSERT INTO {td.name}(code, name_th, description, created_by) VALUES(?,?,?,?)",
                       (code, name, desc, user_id))
        else:
            db.execute(f"UPDATE {td.name} SET code=?, name_th=?, description=?, "
                       f"updated_at=datetime('now','localtime') WHERE id=?", (code, name, desc, row_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return ["รหัสนี้ถูกใช้แล้ว"]
    return []


def toggle_row(db, td: TableDef, row_id: int) -> None:
    db.execute(f"UPDATE {td.name} SET active = 1 - active, updated_at=datetime('now','localtime') WHERE id=?",
               (row_id,))
    db.commit()


def active_options(db, td: TableDef) -> list:
    """รายการสำหรับ dropdown — เฉพาะที่ใช้งานอยู่"""
    return db.execute(f"SELECT id, code, name_th FROM {td.name} WHERE active=1 ORDER BY code").fetchall()
