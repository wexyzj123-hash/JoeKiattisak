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
class ParentDef:
    column: str               # คอลัมน์อ้างอิงแม่ เช่น factory_id (ใช้เป็นชื่อฟิลด์ในฟอร์มด้วย)
    table: str
    label_th: str


@dataclass(frozen=True)
class TableDef:
    name: str                 # ชื่อตารางในฐานข้อมูล
    slug: str                 # ส่วนของ URL
    label_th: str
    parent: ParentDef | None = None


@dataclass
class Page:
    rows: list
    page: int
    pages: int
    total: int
    per_page: int = 20


REGISTRY: dict[str, TableDef] = {td.slug: td for td in (
    TableDef("factories", "factories", "โรงงาน"),
    TableDef("departments", "departments", "แผนก", ParentDef("factory_id", "factories", "โรงงาน")),
    TableDef("lines", "lines", "ไลน์/กลุ่มงาน", ParentDef("department_id", "departments", "แผนก")),
    TableDef("risk_areas", "risk-areas", "พื้นที่เสี่ยง", ParentDef("line_id", "lines", "ไลน์/กลุ่มงาน")),
    TableDef("hazard_types", "hazard-types", "ประเภทอันตราย"),
    TableDef("hazard_sources", "hazard-sources", "แหล่งที่มา"),
    TableDef("incident_causes", "incident-causes", "สาเหตุอุบัติเหตุ"),
)}

# คอลัมน์ที่เรียงได้ (ชื่อจาก URL → นิพจน์ SQL ที่เขียนตายตัว; ข้อความจากผู้ใช้ไม่เคยถูกต่อเข้า SQL)
SORTS = {"code": "t.code", "name_th": "t.name_th", "active": "t.active", "creator": "u.name",
         "created_at": "t.created_at"}


def parent_td(td: TableDef) -> TableDef | None:
    return next((t for t in REGISTRY.values() if td.parent and t.name == td.parent.table), None)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_rows(db, td: TableDef, q: str = "", sort: str = "code", direction: str = "asc",
              page: int = 1, per_page: int = 20) -> Page:
    expr = SORTS.get(sort) or ("p.name_th" if sort == "parent" and td.parent else SORTS["code"])
    order = "DESC" if direction == "desc" else "ASC"
    where, args = "", []
    if q.strip():
        where = " WHERE (t.code LIKE ? ESCAPE '\\' OR t.name_th LIKE ? ESCAPE '\\')"
        args = [_like(q.strip())] * 2
    join = f" LEFT JOIN {td.parent.table} p ON p.id = t.{td.parent.column}" if td.parent else ""
    base = f"FROM {td.name} t LEFT JOIN safety_users u ON u.id = t.created_by{join}{where}"
    total = db.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    rows = db.execute(f"SELECT t.*, u.name AS creator{', p.name_th AS parent_name' if td.parent else ''} {base} ORDER BY {expr} {order}, t.id "
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
    parent_id = None
    if td.parent:
        parent_id, perr = _check_parent(db, td, row_id, form.get(td.parent.column))
        if perr:
            errors.append(perr)
    if not errors and db.execute(f"SELECT 1 FROM {td.name} WHERE code = ? COLLATE NOCASE AND id <> ?",
                                 (code, row_id or 0)).fetchone():
        errors.append("รหัสนี้ถูกใช้แล้ว")
    if errors:
        return errors
    try:
        pcol, pval = (f", {td.parent.column}", (parent_id,)) if td.parent else ("", ())
        if row_id is None:
            db.execute(f"INSERT INTO {td.name}(code, name_th, description, created_by{pcol}) "
                       f"VALUES(?,?,?,?{',?' if td.parent else ''})", (code, name, desc, user_id) + pval)
        else:
            db.execute(f"UPDATE {td.name} SET code=?, name_th=?, description=?"
                       f"{(', ' + td.parent.column + '=?') if td.parent else ''}, "
                       f"updated_at=datetime('now','localtime') WHERE id=?", (code, name, desc) + pval + (row_id,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return ["รหัสนี้ถูกใช้แล้ว"]
    return []


def _check_parent(db, td: TableDef, row_id: int | None, raw) -> tuple[int | None, str]:
    """แม่ต้องมีอยู่จริงและเปิดใช้งาน — ยกเว้นแก้แถวเดิมโดยไม่เปลี่ยนแม่ (แม่ถูกปิดภายหลังก็ยังแก้ลูกได้)"""
    label = td.parent.label_th
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None, f"กรุณาเลือก{label}"
    prow = db.execute(f"SELECT active FROM {td.parent.table} WHERE id=?", (pid,)).fetchone()
    if prow is None:
        return None, f"ไม่พบ{label}ที่เลือก"
    if not prow["active"]:
        current = db.execute(f"SELECT {td.parent.column} FROM {td.name} WHERE id=?", (row_id,)).fetchone() \
            if row_id else None
        if not current or current[0] != pid:
            return None, f"{label}ที่เลือกถูกปิดใช้งานแล้ว"
    return pid, ""


def breadcrumb(db, td: TableDef, row_id: int) -> str:
    """ชื่อจากโรงงานลงมาถึงแถวนี้ คั่นด้วย ' › '"""
    names: list[str] = []
    cur, rid = td, row_id
    while cur is not None and rid is not None:
        row = db.execute(f"SELECT * FROM {cur.name} WHERE id=?", (rid,)).fetchone()
        if row is None:
            break
        names.append(row["name_th"])
        rid = row[cur.parent.column] if cur.parent else None
        cur = parent_td(cur)
    return " › ".join(reversed(names))


def toggle_row(db, td: TableDef, row_id: int) -> None:
    db.execute(f"UPDATE {td.name} SET active = 1 - active, updated_at=datetime('now','localtime') WHERE id=?",
               (row_id,))
    db.commit()


def active_options(db, td: TableDef) -> list:
    """รายการสำหรับ dropdown — เฉพาะที่ใช้งานอยู่"""
    return db.execute(f"SELECT id, code, name_th FROM {td.name} WHERE active=1 ORDER BY code").fetchall()
