"""safety/unit.py — «หน่วยงานของฉัน»: ผู้ใช้ที่ผูกกับแผนก (หรือโรงงาน) เห็นสถานะของรายการอันตราย/อุบัติเหตุในหน่วยงานตนได้ทุกรายการ (อ่านอย่างเดียว)
- ผูกระดับแผนก = เห็นเฉพาะแผนกนั้น; ผูกระดับโรงงาน (ไม่ระบุแผนก) = เห็นทั้งโรงงาน
- ผู้ดำรงตำแหน่งที่ผู้ดูแลกำหนดให้ «แจ้งให้ทราบ» (เช่น GM) เห็นทุกหน่วยงาน
- ผู้ที่ไม่มีขอบเขตและไม่ใช่ตำแหน่งข้างต้น ไม่ได้สิทธิ์เพิ่มจากกติกานี้ (ยังเห็นตามบทบาทเดิม)
ข้อมูลส่วนบุคคลของผู้เกี่ยวข้องในอุบัติเหตุยังเห็นได้เฉพาะผู้สอบสวนและผู้แจ้งเหมือนเดิม"""
from __future__ import annotations

from . import approvals


def sees_all(db, user) -> bool:
    return bool(approvals.position_code(db, user)) and approvals.position_code(db, user) in set(approvals.codes(db, "approval_notify"))


def scope(db, user) -> tuple[str, list] | None:
    """เงื่อนไข SQL (ใช้กับตาราง hazards/incidents ที่มี risk_area_id; ตัวย่อ {a}) ของหน่วยงานผู้ใช้ — None = ไม่ผูกกับหน่วยงานใด"""
    if sees_all(db, user):
        return "1=1", []
    if user["department_id"] is not None:
        return "d.id = ?", [user["department_id"]]
    if user["factory_id"] is not None:
        return "d.factory_id = ?", [user["factory_id"]]
    return None


def clause(db, user, alias: str) -> tuple[str, list] | None:
    sc = scope(db, user)
    if sc is None:
        return None
    sql, args = sc
    if sql == "1=1":
        return "1=1", []
    return (f"{alias}.risk_area_id IN (SELECT ra.id FROM risk_areas ra JOIN lines l ON l.id = ra.line_id "
            f"JOIN departments d ON d.id = l.department_id WHERE {sql})"), args


def member(db, user, rec) -> bool:
    """รายการนี้อยู่ในหน่วยงานของผู้ใช้หรือไม่"""
    c = clause(db, user, "x")
    if c is None:
        return False
    if c[0] == "1=1":
        return True
    sql, args = c
    return db.execute(f"SELECT 1 FROM (SELECT ? AS risk_area_id) x WHERE {sql}", [rec["risk_area_id"]] + args).fetchone() is not None


def label(db, user) -> str:
    if sees_all(db, user):
        return "ทุกหน่วยงาน"
    if user["department_id"] is not None:
        r = db.execute("SELECT f.name_th, d.name_th FROM departments d JOIN factories f ON f.id = d.factory_id WHERE d.id=?", (user["department_id"],)).fetchone()
        return f"{r[0]} › {r[1]}" if r else ""
    if user["factory_id"] is not None:
        r = db.execute("SELECT name_th FROM factories WHERE id=?", (user["factory_id"],)).fetchone()
        return f"{r[0]} (ทั้งโรงงาน)" if r else ""
    return ""


def overview(db, user) -> dict | None:
    """สรุปสถานะหน่วยงานของผู้ใช้: จำนวนตามสถานะ, เกินกำหนด, รออนุมัติ, รายการล่าสุดพร้อมสถานะอนุมัติ — None ถ้าไม่ผูกกับหน่วยงาน"""
    from . import timeutil
    hz, inc = clause(db, user, "h"), clause(db, user, "i")
    if hz is None or inc is None:
        return None
    today = timeutil.today().isoformat()
    h_status = {r[0]: r[1] for r in db.execute(f"SELECT h.status, COUNT(*) FROM hazards h WHERE {hz[0]} GROUP BY h.status", hz[1])}
    i_status = {r[0]: r[1] for r in db.execute(f"SELECT i.status, COUNT(*) FROM incidents i WHERE {inc[0]} GROUP BY i.status", inc[1])}
    overdue = db.execute(f"SELECT COUNT(*) FROM hazards h WHERE {hz[0]} AND h.status IN ('reported','assessed','assigned') AND h.due_date < ?", hz[1] + [today]).fetchone()[0]
    items = []
    for r in db.execute(f"SELECT h.id, h.code, h.title, h.status, h.level_name, h.level_color, h.reported_at AS at, h.risk_area_id, h.location_text, h.due_date "
                        f"FROM hazards h WHERE {hz[0]} ORDER BY h.id DESC LIMIT 40", hz[1]):
        items.append({**dict(r), "kind": "hazard", "kind_th": "อันตราย", "href": f"/hazards/{r['id']}"})
    for r in db.execute(f"SELECT i.id, i.code, i.title, i.status, i.level_name, i.level_color, i.reported_at AS at, i.risk_area_id, '' AS location_text, i.due_date "
                        f"FROM incidents i WHERE {inc[0]} ORDER BY i.id DESC LIMIT 40", inc[1]):
        items.append({**dict(r), "kind": "incident", "kind_th": "อุบัติเหตุ", "href": f"/incidents/{r['id']}"})
    items.sort(key=lambda x: x["at"] or "", reverse=True)
    items = items[:40]
    for it in items:
        it["approval"] = approvals.summary(db, it["kind"], it["id"])
        area = db.execute("SELECT ra.name_th, ra.code, l.name_th AS line FROM risk_areas ra JOIN lines l ON l.id = ra.line_id WHERE ra.id=?", (it["risk_area_id"],)).fetchone()
        it["where"] = (area["line"] if area["code"].startswith("AUTO-") else area["name_th"]) if area else ""
    pending_ap = db.execute("SELECT COUNT(*) FROM approvals a WHERE a.status='pending' AND ((a.kind='hazard' AND a.ref_id IN (SELECT h.id FROM hazards h WHERE " + hz[0] +
                            ")) OR (a.kind='incident' AND a.ref_id IN (SELECT i.id FROM incidents i WHERE " + inc[0] + ")))", hz[1] + inc[1]).fetchone()[0]
    open_h = sum(h_status.get(s, 0) for s in ("reported", "assessed", "assigned"))
    open_i = sum(i_status.get(s, 0) for s in ("reported", "investigating", "actions_set"))
    return {"label": label(db, user), "hazard": h_status, "incident": i_status, "open_hazard": open_h, "open_incident": open_i, "overdue": overdue,
            "pending_approvals": pending_ap, "items": items, "closed_hazard": h_status.get("closed", 0), "closed_incident": i_status.get("closed", 0)}
