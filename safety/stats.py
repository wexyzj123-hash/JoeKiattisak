"""safety/stats.py — ตัวเลขสำหรับแดชบอร์ด; ทุกฟังก์ชันเคารพขอบเขตโรงงาน/แผนกและสิทธิ์การมองเห็นของผู้ใช้
"เปิดอยู่" = reported|assessed|assigned; "เกินกำหนด" = เปิดอยู่และ due_date < วันนี้ (ครบกำหนดวันนี้ยังไม่เกิน)"""
from __future__ import annotations

from datetime import date

from . import timeutil
from .hazards import STATUS_TH, can_view_all, hazard_scope_sql
from .perms import user_permissions
from .risk import max_score

OPEN_SQL = "h.status IN ('reported','assessed','assigned')"
UNASSESSED = ("ยังไม่ประเมิน", "#8fa3bd")


def _visible(db, user) -> tuple[str, list]:
    sql, args = hazard_scope_sql(user)
    if not can_view_all(user_permissions(db, user)):
        sql, args = f"{sql} AND (h.reported_by = ? OR h.owner_id = ?)", args + [user["id"]] * 2
    return sql, args


def counts_by_status(db, user) -> dict[str, int]:
    where, args = _visible(db, user)
    out = {k: 0 for k in STATUS_TH}
    for r in db.execute(f"SELECT h.status, COUNT(*) AS n FROM hazards h WHERE {where} GROUP BY h.status", args):
        out[r["status"]] = r["n"]
    return out


def open_by_level(db, user) -> list[tuple[str, str, int]]:
    where, args = _visible(db, user)
    rows = db.execute(f"SELECT h.level_name, h.level_color, COUNT(*) AS n, MIN(h.score) AS lo FROM hazards h "
                      f"WHERE {where} AND {OPEN_SQL} GROUP BY h.level_name, h.level_color "
                      f"ORDER BY h.level_name IS NULL, lo, h.level_name", args).fetchall()
    return [((r["level_name"], r["level_color"]) if r["level_name"] else UNASSESSED) + (r["n"],) for r in rows]


def overdue_count(db, user) -> int:
    where, args = _visible(db, user)
    return db.execute(f"SELECT COUNT(*) FROM hazards h WHERE {where} AND {OPEN_SQL} AND h.due_date < ?",
                      args + [timeutil.today().isoformat()]).fetchone()[0]


def heatmap(db, user) -> dict[tuple[int, int], int]:
    where, args = _visible(db, user)
    rows = db.execute(f"SELECT h.likelihood, h.severity, COUNT(*) AS n FROM hazards h WHERE {where} "
                      f"AND h.status IN ('assessed','assigned') AND h.likelihood IS NOT NULL GROUP BY h.likelihood, h.severity", args)
    return {(r["likelihood"], r["severity"]): r["n"] for r in rows}


def _months(today: date, count: int) -> list[str]:
    y, m, out = today.year, today.month, []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def monthly_trend(db, user, months: int = 12) -> list[tuple[str, int, int]]:
    """(YYYY-MM, แจ้ง, ปิด) ย้อนหลัง months เดือนรวมเดือนนี้ — เติม 0 ให้ครบ เรียงเก่า→ใหม่; จำนวนปิดนับจากเหตุการณ์ 'closed'"""
    where, args = _visible(db, user)
    keys = _months(timeutil.today(), months)
    reported = {r["m"]: r["n"] for r in db.execute(
        f"SELECT substr(h.reported_at,1,7) AS m, COUNT(*) AS n FROM hazards h WHERE {where} GROUP BY m", args)}
    closed = {r["m"]: r["n"] for r in db.execute(
        f"SELECT substr(e.at,1,7) AS m, COUNT(*) AS n FROM hazard_events e JOIN hazards h ON h.id = e.hazard_id "
        f"WHERE {where} AND e.kind = 'closed' GROUP BY m", args)}
    return [(k, reported.get(k, 0), closed.get(k, 0)) for k in keys]


def by_department(db, user) -> list:
    """ทุกแผนกในขอบเขตของผู้ใช้ (รวมแผนกที่ยังไม่มีรายการ) พร้อมจำนวนเปิดอยู่/เกินกำหนด/ระดับสูงขึ้นไป"""
    vis, vargs = _visible(db, user)
    dconds, dargs = [], []
    if user["factory_id"] is not None:
        dconds.append("d.factory_id = ?"); dargs.append(user["factory_id"])
    if user["department_id"] is not None:
        dconds.append("d.id = ?"); dargs.append(user["department_id"])
    threshold = 0.75 * max_score(db)
    return db.execute(
        f"SELECT f.name_th AS factory, d.name_th AS department, "
        f"COALESCE(SUM(CASE WHEN {OPEN_SQL} THEN 1 ELSE 0 END), 0) AS open, "
        f"COALESCE(SUM(CASE WHEN {OPEN_SQL} AND h.due_date < ? THEN 1 ELSE 0 END), 0) AS overdue, "
        f"COALESCE(SUM(CASE WHEN {OPEN_SQL} AND (h.level_requires_approval = 1 OR h.risk_level_id IN "
        f"(SELECT id FROM risk_levels WHERE max_score >= ?)) THEN 1 ELSE 0 END), 0) AS high_or_above "
        f"FROM departments d JOIN factories f ON f.id = d.factory_id "
        f"LEFT JOIN lines l ON l.department_id = d.id LEFT JOIN risk_areas ra ON ra.line_id = l.id "
        f"LEFT JOIN hazards h ON h.risk_area_id = ra.id AND {vis} "
        f"WHERE {' AND '.join(dconds) or '1=1'} GROUP BY d.id ORDER BY f.code, d.code",
        [timeutil.today().isoformat(), threshold] + vargs + dargs).fetchall()


def global_summary(db) -> tuple[int, int]:
    """(เปิดอยู่, เกินกำหนด) ทั้งระบบ ไม่จำกัดขอบเขต — ใช้กับป้ายบน Hub (service token)"""
    row = db.execute(f"SELECT COUNT(*), COALESCE(SUM(h.due_date < ?), 0) FROM hazards h WHERE {OPEN_SQL}",
                     (timeutil.today().isoformat(),)).fetchone()
    return row[0], int(row[1])


def open_by_type(db, user, top: int = 5) -> list[tuple[str, int]]:
    """รายการเปิดอยู่แยกตามประเภทอันตราย: ประเภทที่มากสุด top อันดับ + 'อื่น ๆ' (ไม่แสดงประเภทที่เป็นศูนย์)"""
    where, args = _visible(db, user)
    rows = db.execute(f"SELECT t.name_th AS name, COUNT(*) AS n FROM hazards h JOIN hazard_types t ON t.id = h.hazard_type_id "
                      f"WHERE {where} AND {OPEN_SQL} GROUP BY t.id ORDER BY n DESC, t.name_th", args).fetchall()
    out = [(r["name"], r["n"]) for r in rows[:top]]
    rest = sum(r["n"] for r in rows[top:])
    return out + [("อื่น ๆ", rest)] if rest else out


def closed_this_month(db, user, today: date | None = None) -> int:
    """จำนวนที่ปิดในเดือนปฏิทินของ today (นับจากเหตุการณ์ 'closed')"""
    where, args = _visible(db, user)
    month = (today or timeutil.today()).strftime("%Y-%m")
    return db.execute(f"SELECT COUNT(*) FROM hazard_events e JOIN hazards h ON h.id = e.hazard_id "
                      f"WHERE {where} AND e.kind = 'closed' AND substr(e.at,1,7) = ?", args + [month]).fetchone()[0]


def high_risk_open(db, user) -> int:
    """รายการเปิดอยู่ที่ระดับความเสี่ยงต้องอนุมัติ (ตาม snapshot ที่บันทึกตอนประเมิน)"""
    where, args = _visible(db, user)
    return db.execute(f"SELECT COUNT(*) FROM hazards h WHERE {where} AND {OPEN_SQL} AND h.level_requires_approval = 1", args).fetchone()[0]


def latest_hazards(db, user, limit: int = 6) -> list:
    where, args = _visible(db, user)
    return db.execute(
        f"SELECT h.id, h.code, h.title, h.status, h.due_date, h.level_name, h.level_color, h.score, a.name_th AS area_name, "
        f"(h.due_date IS NOT NULL AND h.due_date < ? AND {OPEN_SQL}) AS overdue "
        f"FROM hazards h JOIN risk_areas a ON a.id = h.risk_area_id WHERE {where} "
        f"ORDER BY h.reported_at DESC, h.id DESC LIMIT ?", [timeutil.today().isoformat()] + args + [max(1, min(int(limit), 50))]).fetchall()
