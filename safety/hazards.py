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


def user_in_scope(db, user, risk_area_id: int) -> bool:
    """พื้นที่เสี่ยงนี้อยู่ในขอบเขตโรงงาน/แผนกของผู้ใช้หรือไม่ (ไม่มีขอบเขต = ทุกพื้นที่)"""
    conds, args = ["ra.id = ?"], [risk_area_id]
    if user["factory_id"] is not None:
        conds.append("d.factory_id = ?"); args.append(user["factory_id"])
    if user["department_id"] is not None:
        conds.append("d.id = ?"); args.append(user["department_id"])
    return db.execute("SELECT 1 FROM risk_areas ra JOIN lines l ON l.id = ra.line_id JOIN departments d ON d.id = l.department_id "
                      f"WHERE {' AND '.join(conds)}", args).fetchone() is not None


def visible_hazard(db, user, hazard_id: int):
    """แถวอันตรายที่ผู้ใช้มองเห็น หรือ None (ไม่มี/นอกขอบเขต/ผู้แจ้งทั่วไปที่ไม่ใช่เจ้าของ)"""
    sql, args = hazard_scope_sql(user)
    row = db.execute(f"SELECT h.* FROM hazards h WHERE h.id = ? AND {sql}", [hazard_id] + args).fetchone()
    if row is None:
        return None
    if not can_view_all(user_permissions(db, user)) and user["id"] not in (row["reported_by"], row["owner_id"]):
        from . import unit
        if not unit.member(db, user, row):
            return None                                     # ผู้ใช้ทั่วไปเห็นที่ตนแจ้ง/ได้รับมอบหมาย และรายการในหน่วยงานของตน (อ่านอย่างเดียว)
    return row


AUTO_PREFIX = "AUTO-"


def _crumb(db, td, row) -> str:
    text = breadcrumb(db, td, row["id"])
    return text.rsplit(" › ", 1)[0] if row["code"].startswith(AUTO_PREFIX) else text      # พื้นที่อัตโนมัติ = ระดับไลน์ ไม่โชว์คำว่า "ทั่วไป"


def ensure_line_area(db, line_id: int) -> int:
    """ฟอร์มแจ้งอันตรายเลือกถึงระดับไลน์ + พิมพ์สถานที่เอง — ระบบเก็บพื้นที่ภายในระดับไลน์ให้อัตโนมัติ (ใช้กับขอบเขตสิทธิ์/รายงานเดิม)"""
    code = f"{AUTO_PREFIX}{line_id}"
    row = db.execute("SELECT id, active FROM risk_areas WHERE code=?", (code,)).fetchone()
    if row is None:
        cur = db.execute("INSERT INTO risk_areas(code, name_th, line_id, description) VALUES(?,?,?,?)",
                         (code, "ทั่วไป", line_id, "สร้างอัตโนมัติจากฟอร์มแจ้งอันตราย (เลือกถึงระดับไลน์)"))
        return cur.lastrowid
    if not row["active"]:
        db.execute("UPDATE risk_areas SET active=1 WHERE id=?", (row["id"],))
    return row["id"]


def location_history(db, user, limit: int = 50) -> list[str]:
    """สถานที่ที่เคยกรอกในรายการที่ผู้ใช้เห็น (ใช้เป็นตัวช่วยเติมคำ และดูย้อนหลังได้)"""
    scope_sql, args = hazard_scope_sql(user)
    rows = db.execute("SELECT h.location_text AS t, COUNT(*) AS n FROM hazards h WHERE h.location_text <> '' AND " + scope_sql +
                      " GROUP BY h.location_text ORDER BY n DESC, MAX(h.id) DESC LIMIT ?", args + [limit]).fetchall()
    return [r["t"] for r in rows]


def risk_area_options(db, user) -> list:
    """พื้นที่เสี่ยงที่เลือกได้: ตัวมัน ไลน์ แผนก และโรงงานต้องใช้งานอยู่ทั้งหมด และอยู่ในขอบเขตผู้ใช้"""
    conds, args = ["ra.active=1", "l.active=1", "d.active=1", "f.active=1"], []
    if user["factory_id"] is not None:
        conds.append("f.id = ?")
        args.append(user["factory_id"])
    if user["department_id"] is not None:
        conds.append("d.id = ?")
        args.append(user["department_id"])
    rows = db.execute("SELECT ra.id, ra.code, ra.name_th, f.id AS factory_id, d.id AS department_id, l.id AS line_id, d.name_th AS department_name, l.name_th AS line_name FROM risk_areas ra JOIN lines l ON l.id = ra.line_id "
                      "JOIN departments d ON d.id = l.department_id JOIN factories f ON f.id = d.factory_id "
                      f"WHERE {' AND '.join(conds)} ORDER BY f.code, d.code, l.code, ra.code", args).fetchall()
    td = REGISTRY["risk-areas"]
    return [{"id": r["id"], "code": r["code"], "name_th": r["name_th"], "factory_id": r["factory_id"], "department_id": r["department_id"],
             "line_id": r["line_id"], "department_name": r["department_name"], "line_name": r["line_name"], "breadcrumb": _crumb(db, td, r)}
            for r in rows]


def org_options(db, user) -> dict:
    """โรงงาน/แผนก/ไลน์ที่ใช้งานอยู่ภายในขอบเขตผู้ใช้ — ใช้เป็นตัวกรองในฟอร์ม (ไม่ต้องรอให้มีพื้นที่เสี่ยงก่อนจึงจะเห็น)"""
    conds, args = ["f.active=1", "d.active=1", "l.active=1"], []
    if user["factory_id"] is not None:
        conds.append("f.id = ?"); args.append(user["factory_id"])
    if user["department_id"] is not None:
        conds.append("d.id = ?"); args.append(user["department_id"])
    rows = db.execute("SELECT f.id AS fid, f.name_th AS fname, d.id AS did, d.name_th AS dname, l.id AS lid, l.name_th AS lname "
                      "FROM lines l JOIN departments d ON d.id = l.department_id JOIN factories f ON f.id = d.factory_id "
                      f"WHERE {' AND '.join(conds)} ORDER BY f.code, d.code, l.code", args).fetchall()
    fac, dep, lin = {}, {}, []
    for r in rows:
        fac[r["fid"]] = {"id": r["fid"], "name": r["fname"]}
        dep[r["did"]] = {"id": r["did"], "factory_id": r["fid"], "name": r["dname"]}
        lin.append({"id": r["lid"], "department_id": r["did"], "name": r["lname"]})
    return {"factories": list(fac.values()), "departments": list(dep.values()), "lines": lin}


def add_event(db, hazard_id: int, actor_id: int | None, kind: str, from_status: str | None, to_status: str | None,
              note: str = "", data: dict | None = None) -> None:
    """เพิ่มบันทึกเหตุการณ์ (ไม่ commit — ผู้เรียกรวมกับการเปลี่ยนสถานะใน transaction เดียว)"""
    db.execute("INSERT INTO hazard_events(hazard_id, actor_id, kind, from_status, to_status, note, data) "
               "VALUES(?,?,?,?,?,?,?)", (hazard_id, actor_id, kind, from_status, to_status, note,
                                         json.dumps(data, ensure_ascii=False) if data else ""))
