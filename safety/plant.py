"""safety/plant.py — ตัวเลขภาพรวมทั้งโรงงานสำหรับแดชบอร์ดบน Hub (ไม่จำกัดขอบเขต ไม่มีชื่อบุคคล) — เรียกผ่าน service token เท่านั้น"""
from __future__ import annotations

from datetime import date, timedelta

from . import timeutil
from .stats import OPEN_SQL, _high_sql, _months, global_summary


def _n(db, sql, args=()) -> int:
    return db.execute(sql, args).fetchone()[0] or 0


def plant_summary(db) -> dict:
    today = timeutil.today()
    iso, ym = today.isoformat(), f"{today.year:04d}-{today.month:02d}"
    open_h, overdue = global_summary(db)
    hi_sql, hi_args = _high_sql(db)
    live = "i.status != 'rejected'"
    row = db.execute("SELECT MAX(substr(i.occurred_at,1,10)) FROM incidents i JOIN injury_severities s ON s.id = i.injury_severity_id "
                     f"WHERE i.kind='accident' AND {live} AND s.counts_lost_time = 1 AND substr(i.occurred_at,1,10) <= ?", (iso,)).fetchone()
    last = row[0] if row else None
    kinds = {r[0]: r[1] for r in db.execute(f"SELECT i.kind, COUNT(*) FROM incidents i WHERE {live} AND i.occurred_at LIKE ? GROUP BY i.kind", (ym + "%",))}
    avg = db.execute("SELECT AVG(score_pct) FROM patrol_rounds WHERE status='submitted' AND score_pct IS NOT NULL AND substr(submitted_at,1,10) >= ?",
                     ((today - timedelta(days=30)).isoformat(),)).fetchone()[0]
    monthly = []
    for m in _months(today, 12):
        monthly.append({"m": m,
                        "reported": _n(db, "SELECT COUNT(*) FROM hazards h WHERE h.status != 'rejected' AND h.reported_at LIKE ?", (m + "%",)),
                        "closed": _n(db, "SELECT COUNT(DISTINCT hazard_id) FROM hazard_events WHERE to_status='closed' AND at LIKE ?", (m + "%",)),
                        "accidents": _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {live} AND i.kind='accident' AND i.occurred_at LIKE ?", (m + "%",)),
                        "near_miss": _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {live} AND i.kind='near_miss' AND i.occurred_at LIKE ?", (m + "%",))})
    return {
        "open_hazards": open_h, "overdue": overdue,
        "high_risk_open": _n(db, f"SELECT COUNT(*) FROM hazards h WHERE {OPEN_SQL} AND {hi_sql}", hi_args),
        "hazards_month": monthly[-1]["reported"], "closed_month": monthly[-1]["closed"],
        "incidents_month": {"accident": kinds.get("accident", 0), "near_miss": kinds.get("near_miss", 0)},
        "days_without_lost_time": (today - date.fromisoformat(last)).days if last else None, "last_lost_time_date": last,
        "capa_open": _n(db, "SELECT COUNT(*) FROM capas WHERE status IN ('open','in_progress')"),
        "capa_overdue": _n(db, "SELECT COUNT(*) FROM capas WHERE status IN ('open','in_progress') AND due_date < ?", (iso,)),
        "patrol_avg_score_30d": round(avg) if avg is not None else None,
        "monthly": monthly,
    }
