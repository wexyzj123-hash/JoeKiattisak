"""safety/incident_stats.py — สถิติอุบัติเหตุ/Near Miss และ "วันปลอดอุบัติเหตุ"; ไม่มีชื่อบุคคลในผลลัพธ์ใด ๆ
ตัวเลขทั้งหมดนับเฉพาะรายการที่ไม่ถูกปฏิเสธ และเคารพขอบเขตโรงงาน/แผนกของผู้ใช้"""
from __future__ import annotations

from datetime import date

from . import timeutil
from .incidents import INCIDENT_STATUS_TH, can_view_all, scope_sql
from .perms import user_permissions
from .stats import _months

LIVE = "i.status != 'rejected'"


def _visible(db, user) -> tuple[str, list]:
    sql, args = scope_sql(user)
    if not can_view_all(user_permissions(db, user)):
        sql, args = f"{sql} AND (i.reported_by = ? OR i.owner_id = ?)", list(args) + [user["id"]] * 2
    return sql, list(args)


def days_without_lost_time(db, user, today: date | None = None) -> tuple[int | None, str | None]:
    """(จำนวนวัน, วันที่เกิดเหตุล่าสุด) นับจากอุบัติเหตุหยุดงานครั้งล่าสุด ตามขอบเขตของผู้ใช้ (ไม่จำกัดเฉพาะของตนเอง — เป็นตัวเลขรวม ไม่มีข้อมูลบุคคล);
    ธง counts_lost_time อ่านจากตารางปัจจุบัน จึงตามการตั้งค่าล่าสุดของแอดมิน"""
    sql, args = scope_sql(user)
    row = db.execute(f"SELECT MAX(substr(i.occurred_at, 1, 10)) FROM incidents i JOIN injury_severities s ON s.id = i.injury_severity_id "
                     f"WHERE {sql} AND i.kind = 'accident' AND {LIVE} AND s.counts_lost_time = 1", args).fetchone()
    if row is None or row[0] is None:
        return None, None
    last = date.fromisoformat(row[0])
    return (today or timeutil.today()).toordinal() - last.toordinal(), row[0]


def counts_by_kind_status(db, user) -> dict[str, dict[str, int]]:
    where, args = _visible(db, user)
    out = {k: {s: 0 for s in INCIDENT_STATUS_TH} for k in ("accident", "near_miss")}
    for r in db.execute(f"SELECT i.kind, i.status, COUNT(*) AS n FROM incidents i WHERE {where} GROUP BY i.kind, i.status", args):
        out[r["kind"]][r["status"]] = r["n"]
    return out


def monthly_incidents(db, user, months: int = 12) -> list[tuple[str, int, int]]:
    """(YYYY-MM, อุบัติเหตุ, Near Miss) ตามเดือนที่เกิดเหตุ ย้อนหลัง months เดือนรวมเดือนนี้ — เติม 0 ให้ครบ เรียงเก่า→ใหม่"""
    where, args = _visible(db, user)
    keys = _months(timeutil.today(), months)
    got: dict[tuple[str, str], int] = {}
    for r in db.execute(f"SELECT substr(i.occurred_at, 1, 7) AS m, i.kind, COUNT(*) AS n FROM incidents i WHERE {where} AND {LIVE} "
                        f"GROUP BY m, i.kind", args):
        got[(r["m"], r["kind"])] = r["n"]
    return [(k, got.get((k, "accident"), 0), got.get((k, "near_miss"), 0)) for k in keys]


def by_severity(db, user) -> list[tuple[str, int]]:
    """อุบัติเหตุแยกตามระดับการบาดเจ็บ (ชื่อจากตารางปัจจุบัน เรียงตามลำดับ)"""
    where, args = _visible(db, user)
    rows = db.execute(f"SELECT s.name_th AS name, COUNT(*) AS n FROM incidents i JOIN injury_severities s ON s.id = i.injury_severity_id "
                      f"WHERE {where} AND i.kind = 'accident' AND {LIVE} GROUP BY s.id ORDER BY s.rank, s.id", args).fetchall()
    return [(r["name"], r["n"]) for r in rows]


def by_area(db, user, limit: int = 8) -> list[tuple[str, int]]:
    where, args = _visible(db, user)
    rows = db.execute(f"SELECT a.name_th AS name, COUNT(*) AS n FROM incidents i JOIN risk_areas a ON a.id = i.risk_area_id "
                      f"WHERE {where} AND {LIVE} GROUP BY a.id ORDER BY n DESC, a.name_th LIMIT ?", args + [max(1, min(int(limit), 50))]).fetchall()
    return [(r["name"], r["n"]) for r in rows]
