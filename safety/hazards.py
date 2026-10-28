"""safety/hazards.py — ทะเบียนอันตราย: รหัส, การมองเห็นตามสิทธิ์/ขอบเขต, ตัวเลือกพื้นที่เสี่ยง, บันทึกเหตุการณ์"""
from __future__ import annotations

import json
from datetime import date

from . import timeutil
from .master import REGISTRY, breadcrumb
from .perms import user_permissions

STATUS_TH = {"reported": "แจ้งแล้ว", "assessed": "ประเมินแล้ว", "assigned": "มอบหมายแล้ว", "closed": "ปิดแล้ว", "rejected": "ปฏิเสธ"}
EVENT_TH = {"reported": "แจ้งอันตราย", "assessed": "ประเมินความเสี่ยง", "assigned": "มอบหมาย", "closed": "ปิดรายการ", "rejected": "ปฏิเสธรายการ"}
VIEW_ALL_PERMS = {"hazard.assess", "hazard.assign", "hazard.close", "master.view"}


def next_code(db, today: date | None = None) -> str:
    """HZ-YYMM-#### ลำดับเริ่มใหม่ทุกเดือน; ผู้เรียกลองใหม่หนึ่งครั้งถ้าชนกัน (IntegrityError)"""
    today = today or timeutil.today()
    prefix = f"HZ-{today:%y%m}-"
    row = db.execute("SELECT code FROM hazards WHERE code LIKE ? ORDER BY code DESC LIMIT 1", (prefix + "%",)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(row["code"][len(prefix):]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def can_view_all(perms: set[str]) -> bool:
    return bool(perms & VIEW_ALL_PERMS)


def hazard_scope_sql(user) -> tuple[str, list]:
    """เงื่อนไข SQL จำกัด hazards (ตัวย่อ h) ตามโรงงาน/แผนกของผู้ใช้; ไม่มีขอบเขต = ทั้งหมด"""
    conds, args = [], []
    if user["factory_id"] is not None:
        conds.append("d.factory_id = ?")
        args.append(user["factory_id"])
    if user["department_id"] is not None:
        conds.append("d.id = ?")
        args.append(user["department_id"])
    if not conds:
        return "1=1", []
    return ("h.risk_area_id IN (SELECT ra.id FROM risk_areas ra JOIN lines l ON l.id = ra.line_id "
            "JOIN departments d ON d.id = l.department_id WHERE " + " AND ".join(conds) + ")"), args


def visible_hazard(db, user, hazard_id: int):
    """แถวอันตรายที่ผู้ใช้มองเห็น หรือ None (ไม่มี/นอกขอบเขต/ผู้แจ้งทั่วไปที่ไม่ใช่เจ้าของ)"""
    sql, args = hazard_scope_sql(user)
    row = db.execute(f"SELECT h.* FROM hazards h WHERE h.id = ? AND {sql}", [hazard_id] + args).fetchone()
    if row is None:
        return None
    if not can_view_all(user_permissions(db, user)) and row["reported_by"] != user["id"]:
        return None
    return row


def risk_area_options(db, user) -> list:
    """พื้นที่เสี่ยงที่เลือกได้: ตัวมัน ไลน์ แผนก และโรงงานต้องใช้งานอยู่ทั้งหมด และอยู่ในขอบเขตผู้ใช้"""
    conds, args = ["ra.active=1", "l.active=1", "d.active=1", "f.active=1"], []
    if user["factory_id"] is not None:
        conds.append("f.id = ?")
        args.append(user["factory_id"])
    if user["department_id"] is not None:
        conds.append("d.id = ?")
        args.append(user["department_id"])
    rows = db.execute("SELECT ra.id, ra.code, ra.name_th FROM risk_areas ra JOIN lines l ON l.id = ra.line_id "
                      "JOIN departments d ON d.id = l.department_id JOIN factories f ON f.id = d.factory_id "
                      f"WHERE {' AND '.join(conds)} ORDER BY f.code, d.code, l.code, ra.code", args).fetchall()
    td = REGISTRY["risk-areas"]
    return [{"id": r["id"], "code": r["code"], "name_th": r["name_th"], "breadcrumb": breadcrumb(db, td, r["id"])}
            for r in rows]


def add_event(db, hazard_id: int, actor_id: int | None, kind: str, from_status: str | None, to_status: str | None,
              note: str = "", data: dict | None = None) -> None:
    """เพิ่มบันทึกเหตุการณ์ (ไม่ commit — ผู้เรียกรวมกับการเปลี่ยนสถานะใน transaction เดียว)"""
    db.execute("INSERT INTO hazard_events(hazard_id, actor_id, kind, from_status, to_status, note, data) "
               "VALUES(?,?,?,?,?,?,?)", (hazard_id, actor_id, kind, from_status, to_status, note,
                                         json.dumps(data, ensure_ascii=False) if data else ""))
