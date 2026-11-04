"""safety/views/overview.py — แดชบอร์ด: ภาพรวมดำเนินงาน และภาพรวมตามหน่วยงาน (ไม่ใช้ไลบรารีกราฟภายนอก)"""
from __future__ import annotations

from flask import Blueprint, g, render_template

from .. import stats
from ..db import get_db
from ..hazards import STATUS_TH
from ..perms import permission_any
from ..risk import level_for_score, score_bounds

bp = Blueprint("overview", __name__)
ANY_HAZARD_PERM = ("hazard.report", "hazard.assess", "hazard.assign", "hazard.close", "master.view")


@bp.route("/overview")
@permission_any(*ANY_HAZARD_PERM)
def overview():
    db, user = get_db(), g.user
    counts = stats.counts_by_status(db, user)
    levels = stats.open_by_level(db, user)
    cells = stats.heatmap(db, user)
    lik = db.execute("SELECT level, label_th, points FROM risk_scales WHERE kind='likelihood' ORDER BY level").fetchall()
    sev = db.execute("SELECT level, label_th, points FROM risk_scales WHERE kind='severity' ORDER BY level").fetchall()
    mids = db.execute("SELECT points FROM risk_scales WHERE kind='measure' ORDER BY level").fetchall()
    mid = mids[len(mids) // 2][0] if mids else 0               # สีของช่อง: สมมติมาตรการระดับกลาง (แผนที่นี้มีสองแกน)
    grid = []
    for s in sorted(sev, key=lambda r: -r["points"]):          # แถวบน = ความรุนแรงสูงสุด
        row = []
        for l in sorted(lik, key=lambda r: r["points"]):
            score = l["points"] + s["points"] + mid
            level = level_for_score(db, score)
            row.append({"l": l["level"], "s": s["level"], "lname": l["label_th"], "sname": s["label_th"], "score": score,
                        "color": level["color"] if level else "#444", "count": cells.get((l["level"], s["level"]), 0)})
        grid.append(row)
    trend = stats.monthly_trend(db, user)
    peak = max([1] + [max(r, c) for _, r, c in trend])
    return render_template(
        "overview.html", counts=counts, status_th=STATUS_TH, open_total=counts["reported"] + counts["assessed"] + counts["assigned"],
        overdue=stats.overdue_count(db, user), levels=levels, level_peak=max([1] + [n for _, _, n in levels]),
        grid=grid, likelihoods=[c["lname"] for c in grid[0]] if grid else [], trend=trend, trend_peak=peak)


@bp.route("/overview/departments")
@permission_any(*ANY_HAZARD_PERM)
def overview_departments():
    return render_template("overview_departments.html", rows=stats.by_department(get_db(), g.user))
