"""safety/drawer.py — ข้อมูลสรุปในลิ้นชักข้าง (drawer) ของแดชบอร์ด: ตัวชี้วัดหลัก + แถบสถานะ (stepper) + เหตุการณ์ล่าสุด
ใช้ตัวตรวจการมองเห็นเดียวกับหน้ารายละเอียดจริง (ขอบเขตโรงงาน/แผนก + สิทธิ์); ไม่แสดงชื่อผู้เกี่ยวข้อง/ผู้บาดเจ็บ; คำอธิบายถูกตัดให้สั้น"""
from __future__ import annotations

from . import capa as capa_mod, patrol, timeutil
from .hazards import EVENT_TH as HAZARD_EVENT_TH, STATUS_TH as HAZARD_STATUS_TH, visible_hazard
from .incidents import INCIDENT_EVENT_TH, INCIDENT_KIND_TH, INCIDENT_STATUS_TH, visible_incident

DESC_CUT = 240
FLOWS = {
    "hazard": (("reported", "แจ้งแล้ว"), ("assessed", "ประเมินแล้ว"), ("assigned", "มอบหมายแล้ว"), ("closed", "ปิดแล้ว")),
    "incident": (("reported", "แจ้งแล้ว"), ("investigating", "สอบสวน"), ("actions_set", "กำหนดมาตรการ"), ("closed", "ปิดแล้ว")),
    "capa": (("open", "เปิด"), ("in_progress", "ดำเนินการ"), ("done", "เสร็จ"), ("verified", "ยืนยันผล")),
    "patrol": (("draft", "ร่าง"), ("submitted", "ส่งแล้ว")),
}
TERMINAL_BAD = {"hazard": {"rejected": "ปฏิเสธ"}, "incident": {"rejected": "ปฏิเสธ"}, "capa": {"cancelled": "ยกเลิก"}, "patrol": {}}
STATUS_TH = {"hazard": HAZARD_STATUS_TH, "incident": INCIDENT_STATUS_TH, "capa": capa_mod.STATUS_TH, "patrol": {"draft": "ร่าง", "submitted": "ส่งแล้ว"}}


def stepper(kind: str, status: str) -> list[dict]:
    flow = FLOWS.get(kind, ())
    keys = [k for k, _ in flow]
    if status in TERMINAL_BAD.get(kind, {}):
        return [{"key": flow[0][0], "label": flow[0][1], "state": "done"}, {"key": status, "label": TERMINAL_BAD[kind][status], "state": "bad"}]
    if status not in keys:
        return []
    idx = keys.index(status)
    done_all = (kind == "patrol" and status == "submitted") or (kind == "capa" and status == "verified") or status == "closed"   # ขั้นสุดท้ายที่ครบแล้ว
    out = []
    for i, (k, label) in enumerate(flow):
        state = "done" if (i < idx or (i == idx and done_all)) else ("current" if i == idx else "todo")
        out.append({"key": k, "label": label, "state": state})
    return out


def _cut(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= DESC_CUT else text[:DESC_CUT].rstrip() + "…"


def _events(db, table: str, fk: str, ident: int, labels: dict, limit: int = 5) -> list[dict]:
    rows = db.execute(f"SELECT kind, at FROM {table} WHERE {fk}=? ORDER BY at DESC, id DESC LIMIT ?", (ident, limit)).fetchall()
    return [{"label": labels.get(r["kind"], r["kind"]), "at": r["at"]} for r in rows]


def build(db, user, kind: str, ident: int) -> dict | None:
    today = timeutil.today().isoformat()
    if kind == "hazard":
        h = visible_hazard(db, user, ident)
        if h is None:
            return None
        meta = db.execute("SELECT a.name_th AS area, t.name_th AS type, o.name AS owner FROM hazards x JOIN risk_areas a ON a.id = x.risk_area_id "
                          "JOIN hazard_types t ON t.id = x.hazard_type_id LEFT JOIN safety_users o ON o.id = x.owner_id WHERE x.id=?", (ident,)).fetchone()
        overdue = bool(h["due_date"] and h["due_date"] < today and h["status"] in ("reported", "assessed", "assigned"))
        facts = [("พื้นที่", meta["area"]), ("ประเภท", meta["type"]), ("ผู้รับผิดชอบ", meta["owner"] or "ยังไม่มอบหมาย"), ("แจ้งเมื่อ", (h["reported_at"] or "")[:16]),
                 ("ครบกำหนด", h["due_date"] or "—")]
        chips = [{"text": h["level_name"], "color": h["level_color"]}] if h["level_name"] else []
        return {"kind": "hazard", "kind_th": "อันตราย", "id": ident, "code": h["code"], "title": h["title"], "status": h["status"], "status_th": HAZARD_STATUS_TH[h["status"]],
                "steps": stepper("hazard", h["status"]), "facts": facts, "chips": chips, "overdue": overdue, "excerpt": _cut(h["description"]),
                "events": _events(db, "hazard_events", "hazard_id", ident, HAZARD_EVENT_TH), "href": f"/hazards/{ident}"}
    if kind == "incident":
        i = visible_incident(db, user, ident)
        if i is None:
            return None
        meta = db.execute("SELECT a.name_th AS area, s.name_th AS sev, o.name AS owner FROM incidents x JOIN risk_areas a ON a.id = x.risk_area_id "
                          "LEFT JOIN injury_severities s ON s.id = x.injury_severity_id LEFT JOIN safety_users o ON o.id = x.owner_id WHERE x.id=?", (ident,)).fetchone()
        overdue = bool(i["due_date"] and i["due_date"] < today and i["status"] in ("investigating", "actions_set"))
        facts = [("ประเภท", INCIDENT_KIND_TH.get(i["kind"], i["kind"])), ("พื้นที่", meta["area"]), ("เกิดเหตุ", (i["occurred_at"] or "")[:16]),
                 ("ระดับการบาดเจ็บ", meta["sev"] or "—"), ("ผู้สอบสวน", meta["owner"] or "ยังไม่มอบหมาย"), ("ครบกำหนด", i["due_date"] or "—")]
        chips = [{"text": i["level_name"], "color": i["level_color"]}] if i["level_name"] else []
        return {"kind": "incident", "kind_th": INCIDENT_KIND_TH.get(i["kind"], "เหตุการณ์"), "id": ident, "code": i["code"], "title": i["title"], "status": i["status"],
                "status_th": INCIDENT_STATUS_TH[i["status"]], "steps": stepper("incident", i["status"]), "facts": facts, "chips": chips, "overdue": overdue,
                "excerpt": _cut(i["description"]), "events": _events(db, "incident_events", "incident_id", ident, INCIDENT_EVENT_TH), "href": f"/incidents/{ident}"}
    if kind == "capa":
        c = capa_mod.visible_capa(db, user, ident)
        if c is None:
            return None
        meta = db.execute("SELECT a.name_th AS area, o.name AS owner FROM capas x JOIN risk_areas a ON a.id = x.risk_area_id JOIN safety_users o ON o.id = x.owner_id WHERE x.id=?", (ident,)).fetchone()
        overdue = bool(c["due_date"] < today and c["status"] in ("open", "in_progress"))
        facts = [("ประเภท", capa_mod.TYPE_TH.get(c["action_type"], c["action_type"])), ("พื้นที่", meta["area"]), ("เจ้าของงาน", meta["owner"]), ("ครบกำหนด", c["due_date"])]
        return {"kind": "capa", "kind_th": "CAPA", "id": ident, "code": c["code"], "title": c["title"], "status": c["status"], "status_th": capa_mod.STATUS_TH[c["status"]],
                "steps": stepper("capa", c["status"]), "facts": facts, "chips": [], "overdue": overdue, "excerpt": _cut(c["description"]),
                "events": _events(db, "capa_events", "capa_id", ident, capa_mod.EVENT_TH), "href": f"/capas/{ident}"}
    if kind == "patrol":
        r = patrol.visible_round(db, user, ident)
        if r is None:
            return None
        meta = db.execute("SELECT a.name_th AS area, c.name_th AS cl, u.name AS who FROM patrol_rounds x JOIN risk_areas a ON a.id = x.risk_area_id "
                          "JOIN checklists c ON c.id = x.checklist_id JOIN safety_users u ON u.id = x.inspector_id WHERE x.id=?", (ident,)).fetchone()
        facts = [("แบบตรวจ", meta["cl"]), ("พื้นที่", meta["area"]), ("ผู้ตรวจ", meta["who"]), ("วันที่", (r["submitted_at"] or r["started_at"] or "")[:16]),
                 ("ผลการตรวจ", f"ผ่าน {r['passed']} · ไม่ผ่าน {r['failed']}" + (f" · {r['score_pct']}%" if r["score_pct"] is not None else ""))]
        chips = [{"text": f"ไม่ผ่าน {r['failed']}", "color": "#ff4d4f"}] if r["failed"] else ([{"text": "ผ่านทั้งหมด", "color": "#2ecc71"}] if r["status"] == "submitted" else [])
        return {"kind": "patrol", "kind_th": "Safety Patrol", "id": ident, "code": r["code"], "title": meta["cl"], "status": r["status"], "status_th": STATUS_TH["patrol"][r["status"]],
                "steps": stepper("patrol", r["status"]), "facts": facts, "chips": chips, "overdue": False, "excerpt": "", "events": [], "href": f"/patrols/{ident}"}
    return None
