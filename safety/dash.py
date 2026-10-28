"""safety/dash.py — ข้อมูลหน้าภาพรวม (แดชบอร์ด) ที่คลิกเจาะรายละเอียดได้: KPI พร้อมแนวโน้ม, ท่อสถานะ, ประเภท/พื้นที่ที่เปิดอยู่, ความเคลื่อนไหวล่าสุด
ทุกฟังก์ชันเคารพขอบเขตโรงงาน/แผนกและสิทธิ์การมองเห็นเดียวกับหน้ารายการ; ไม่มีชื่อบุคคลในผลลัพธ์"""
from __future__ import annotations

from . import capa, incident_stats, stats, timeutil
from .hazards import EVENT_TH as HAZARD_EVENT_TH
from .incidents import INCIDENT_EVENT_TH
from .perms import user_permissions


def _delta(value: int, prev: int):
    return round((value - prev) / prev * 100, 1) if prev else None


def _top(db, user, table_join: str, id_col: str, name_col: str, limit: int = 6) -> list[dict]:
    where, args = stats._visible(db, user)
    rows = db.execute(f"SELECT {id_col} AS id, {name_col} AS name, COUNT(*) AS n FROM hazards h {table_join} WHERE {where} AND {stats.OPEN_SQL} "
                      f"GROUP BY {id_col} ORDER BY n DESC, {name_col} LIMIT ?", args + [limit]).fetchall()
    return [{"id": r["id"], "name": r["name"], "n": r["n"]} for r in rows]


def _activity(db, user, limit: int) -> list[dict]:
    perms = user_permissions(db, user)
    items = []
    hw, ha = stats._visible(db, user)
    for r in db.execute(f"SELECT e.hazard_id AS id, e.kind, e.at, h.code, h.title FROM hazard_events e JOIN hazards h ON h.id = e.hazard_id WHERE {hw} "
                        "ORDER BY e.at DESC, e.id DESC LIMIT ?", ha + [limit]):
        items.append({"kind": "hazard", "id": r["id"], "at": r["at"], "label": HAZARD_EVENT_TH.get(r["kind"], r["kind"]), "code": r["code"], "title": r["title"], "href": f"/hazards/{r['id']}"})
    iw, ia = incident_stats._visible(db, user)
    for r in db.execute(f"SELECT e.incident_id AS id, e.kind, e.at, i.code, i.title FROM incident_events e JOIN incidents i ON i.id = e.incident_id WHERE {iw} "
                        "ORDER BY e.at DESC, e.id DESC LIMIT ?", ia + [limit]):
        items.append({"kind": "incident", "id": r["id"], "at": r["at"], "label": INCIDENT_EVENT_TH.get(r["kind"], r["kind"]), "code": r["code"], "title": r["title"], "href": f"/incidents/{r['id']}"})
    cs, ca = capa.scope_sql(user)
    if not perms & {"capa.create", "capa.verify"}:
        cs, ca = f"{cs} AND (c.owner_id = ? OR c.created_by = ?)", ca + [user["id"]] * 2
    for r in db.execute(f"SELECT e.capa_id AS id, e.kind, e.at, c.code, c.title FROM capa_events e JOIN capas c ON c.id = e.capa_id WHERE {cs} "
                        "ORDER BY e.at DESC, e.id DESC LIMIT ?", ca + [limit]):
        items.append({"kind": "capa", "id": r["id"], "at": r["at"], "label": capa.EVENT_TH.get(r["kind"], r["kind"]), "code": r["code"], "title": r["title"], "href": f"/capas/{r['id']}"})
    items.sort(key=lambda x: x["at"], reverse=True)
    return items[:limit]


def home_data(db, user, limit_activity: int = 10) -> dict:
    trend = stats.monthly_trend(db, user, 12)
    inc = {m: (a, n) for m, a, n in incident_stats.monthly_incidents(db, user, 12)}
    months = [{"m": m, "reported": r, "closed": c, "accidents": inc.get(m, (0, 0))[0], "near_miss": inc.get(m, (0, 0))[1]} for m, r, c in trend]
    reported, closed = [m["reported"] for m in months], [m["closed"] for m in months]
    counts = stats.counts_by_status(db, user)
    return {
        "months": months,
        "kpi": {"reported": {"value": reported[-1], "prev": reported[-2], "delta_pct": _delta(reported[-1], reported[-2]), "series": reported},
                "closed": {"value": closed[-1], "prev": closed[-2], "delta_pct": _delta(closed[-1], closed[-2]), "series": closed}},
        "pipeline": {"reported": counts["reported"], "assessed": counts["assessed"], "assigned": counts["assigned"], "closed_month": stats.closed_this_month(db, user)},
        "top_types": _top(db, user, "JOIN hazard_types t ON t.id = h.hazard_type_id", "t.id", "t.name_th"),
        "top_areas": _top(db, user, "JOIN risk_areas a ON a.id = h.risk_area_id", "a.id", "a.name_th"),
        "activity": _activity(db, user, limit_activity),
    }
