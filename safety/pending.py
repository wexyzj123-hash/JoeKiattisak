"""safety/pending.py — สรุปงานค้างของผู้ใช้ (ใช้กับหน้าต่างเด้งบนหน้าหลัก): ค้างอะไร ที่ไหน อยู่ขั้นตอนไหน และกดเข้าไปดูได้ทุกรายการ
- «รอคุณดำเนินการ»: สิ่งที่ถึงตาคุณ (ประเมิน/มอบหมาย/แก้ไขและปิด/สอบสวน/CAPA/อนุมัติ)
- «ค้างในหน่วยงานของฉัน»: รายการเปิดอยู่ของหน่วยงานที่ไม่ได้รอคุณ พร้อมขั้นตอนปัจจุบัน
เห็นเฉพาะรายการที่ผู้ใช้เปิดดูได้อยู่แล้ว (ใช้ขอบเขตและสิทธิ์ชุดเดียวกับหน้ารายการ)"""
from __future__ import annotations

from . import approvals, capa, timeutil, unit
from .hazards import STATUS_TH as HZ_TH, can_view_all, hazard_scope_sql
from .incidents import INCIDENT_STATUS_TH as INC_TH, scope_sql as inc_scope, can_view_all as inc_can_view_all
from .perms import user_permissions

LIMIT = 30
HZ_STEPS = (("reported", "แจ้งแล้ว"), ("assessed", "ประเมินแล้ว"), ("assigned", "มอบหมายแล้ว"), ("closed", "ปิดรายการ"))
INC_STEPS = (("reported", "แจ้งแล้ว"), ("investigating", "สอบสวน"), ("actions_set", "กำหนดมาตรการ"), ("closed", "ปิดรายการ"))
CAPA_STEPS = (("open", "เปิด"), ("in_progress", "กำลังดำเนินการ"), ("done", "เสร็จ รอยืนยัน"), ("verified", "ยืนยันแล้ว"))


def _steps(steps, current):
    keys = [k for k, _ in steps]
    idx = keys.index(current) if current in keys else 0
    return [{"label": label, "state": "done" if i < idx else ("now" if i == idx else "todo")} for i, (_, label) in enumerate(steps)]


def _where(db, area_id, location="") -> str:
    row = db.execute("SELECT ra.name_th, ra.code, l.name_th AS line FROM risk_areas ra JOIN lines l ON l.id = ra.line_id WHERE ra.id=?", (area_id,)).fetchone()
    base = (row["line"] if row["code"].startswith("AUTO-") else row["name_th"]) if row else ""
    return f"{base} · {location}" if location else base


def _approval_text(db, kind, ref_id) -> str:
    s = approvals.summary(db, kind, ref_id)
    if s is None:
        return ""
    parts = [f"ขั้น {r['step']} {approvals.STATUS_TH[r['status']]}" for r in s["rows"]]
    return "อนุมัติ: " + " › ".join(parts)


def _days_over(due, today) -> int:
    from datetime import date
    try:
        return max(0, (today - date.fromisoformat(due)).days) if due else 0
    except ValueError:
        return 0


def collect(db, user) -> dict:
    perms = user_permissions(db, user)
    today = timeutil.today()
    mine, others = [], []

    # ---- รออนุมัติ ----
    for m in approvals.pending_for(db, user):
        rec = m["rec"]
        kind_th = "อันตราย" if m["kind"] == "hazard" else "อุบัติเหตุ"
        mine.append({"kind": m["kind"], "kind_th": kind_th, "code": rec["code"], "title": rec["title"], "where": _where(db, rec["risk_area_id"]),
                     "stage": f"รออนุมัติขั้นที่ {m['step']} ({m['step_label']})", "steps": [], "overdue": 0, "extra": "คุณเป็นผู้อนุมัติขั้นนี้"
                     + (" (อนุมัติแทน)" if m["mode"] == "proxy" else ""), "href": "/approvals", "level": rec["level_name"] if "level_name" in rec.keys() else None, "color": None})

    # ---- อันตราย ----
    sql, args = hazard_scope_sql(user)
    conds, cargs = [sql, "h.status IN ('reported','assessed','assigned')"], list(args)
    if not can_view_all(perms):
        vis, vargs = "h.reported_by = ? OR h.owner_id = ?", [user["id"]] * 2
        uc = unit.clause(db, user, "h")
        if uc is not None:
            vis, vargs = f"{vis} OR ({uc[0]})", vargs + uc[1]
        conds.append(f"({vis})"); cargs += vargs
    for h in db.execute(f"SELECT h.* FROM hazards h WHERE {' AND '.join(conds)} ORDER BY h.due_date IS NULL, h.due_date, h.id DESC LIMIT 200", cargs):
        action = None
        if h["status"] == "reported" and "hazard.assess" in perms:
            action = "รอคุณประเมินความเสี่ยง"
        elif h["status"] == "assessed" and "hazard.assign" in perms:
            action = "รอคุณมอบหมายผู้รับผิดชอบ"
        elif h["status"] == "assigned" and h["owner_id"] == user["id"]:
            action = "รอคุณดำเนินการแก้ไขและปิดรายการ"
        elif h["status"] == "assigned" and "hazard.close" in perms:
            action = "รอคุณตรวจและปิดรายการ"
        over = _days_over(h["due_date"], today) if h["status"] == "assigned" else 0
        item = {"kind": "hazard", "kind_th": "อันตราย", "code": h["code"], "title": h["title"], "where": _where(db, h["risk_area_id"], h["location_text"] or ""),
                "stage": HZ_TH[h["status"]], "steps": _steps(HZ_STEPS, h["status"]), "overdue": over, "extra": _approval_text(db, "hazard", h["id"]),
                "href": f"/hazards/{h['id']}", "level": h["level_name"], "color": h["level_color"], "action": action}
        (mine if action else others).append(item)

    # ---- อุบัติเหตุ ----
    isql, iargs = inc_scope(user)
    iconds, icargs = [isql, "i.status IN ('reported','investigating','actions_set')"], list(iargs)
    if not inc_can_view_all(perms):
        vis, vargs = "i.reported_by = ? OR i.owner_id = ?", [user["id"]] * 2
        uc = unit.clause(db, user, "i")
        if uc is not None:
            vis, vargs = f"{vis} OR ({uc[0]})", vargs + uc[1]
        iconds.append(f"({vis})"); icargs += vargs
    for i in db.execute(f"SELECT i.* FROM incidents i WHERE {' AND '.join(iconds)} ORDER BY i.id DESC LIMIT 200", icargs):
        action = None
        if i["status"] == "reported" and "incident.investigate" in perms:
            action = "รอคุณมอบหมายผู้สอบสวน"
        elif i["status"] in ("investigating",) and i["owner_id"] == user["id"]:
            action = "รอคุณบันทึกผลสอบสวน"
        elif i["status"] == "actions_set" and "incident.close" in perms:
            action = "รอคุณปิดรายการ"
        kind_th = "Near Miss" if i["kind"] == "near_miss" else "อุบัติเหตุ"
        item = {"kind": "incident", "kind_th": kind_th, "code": i["code"], "title": i["title"], "where": _where(db, i["risk_area_id"]),
                "stage": INC_TH[i["status"]], "steps": _steps(INC_STEPS, i["status"]), "overdue": _days_over(i["due_date"], today) if i["status"] != "reported" else 0,
                "extra": _approval_text(db, "incident", i["id"]), "href": f"/incidents/{i['id']}", "level": i["level_name"], "color": i["level_color"], "action": action}
        (mine if action else others).append(item)

    # ---- CAPA ----
    where, wargs = capa._visible_where(db, user)
    for c in db.execute(f"SELECT c.* FROM capas c WHERE {where} AND c.status IN ('open','in_progress','done') ORDER BY c.due_date LIMIT 100", wargs):
        action = None
        if c["status"] in ("open", "in_progress") and c["owner_id"] == user["id"]:
            action = "รอคุณดำเนินการ CAPA"
        elif c["status"] == "done" and "capa.verify" in perms:
            action = "รอคุณยืนยันผล CAPA"
        item = {"kind": "capa", "kind_th": "CAPA", "code": c["code"], "title": c["title"], "where": _where(db, c["risk_area_id"]), "stage": capa.STATUS_TH[c["status"]],
                "steps": _steps(CAPA_STEPS, c["status"]), "overdue": _days_over(c["due_date"], today) if c["status"] in ("open", "in_progress") else 0, "extra": f"ครบกำหนด {c['due_date']}",
                "href": f"/capas/{c['id']}", "level": None, "color": None, "action": action}
        (mine if action else others).append(item)

    mine_codes = {x["code"] for x in mine}
    others = [x for x in others if x["code"] not in mine_codes]            # รายการที่รออนุมัติจากคุณแสดงข้างบนแล้ว ไม่ซ้ำในส่วนหน่วยงาน
    for lst in (mine, others):
        lst.sort(key=lambda x: (-x["overdue"], x["code"]))
    # รายการ «ค้างในหน่วยงาน» แสดงเฉพาะผู้ที่ผูกกับหน่วยงาน (ผู้อื่นเห็นเฉพาะที่เกี่ยวกับตน)
    in_unit = unit.scope(db, user) is not None
    overdue_total = sum(1 for x in mine + others if x["overdue"])
    return {"mine": mine[:LIMIT], "mine_total": len(mine), "others": others[:LIMIT] if in_unit else [], "others_total": len(others) if in_unit else 0,
            "overdue_total": overdue_total, "empty": not mine and not (others and in_unit), "unit_label": unit.label(db, user) if in_unit else ""}


def summary_count(db, user) -> int:
    d = collect(db, user)
    return d["mine_total"]
