"""safety/views/overview.py — แดชบอร์ด: ภาพรวมดำเนินงาน และภาพรวมตามหน่วยงาน (ไม่ใช้ไลบรารีกราฟภายนอก)"""
from __future__ import annotations

from flask import Blueprint, g, render_template

from .. import stats
from ..db import get_db
from ..hazards import STATUS_TH
from ..perms import permission_any
from ..risk import level_for_score, scale_size

bp = Blueprint("overview", __name__)
ANY_HAZARD_PERM = ("hazard.report", "hazard.assess", "hazard.assign", "hazard.close", "master.view")


@bp.route("/overview")
@permission_any(*ANY_HAZARD_PERM)
def overview():
    db, user = get_db(), g.user
    counts = stats.counts_by_status(db, user)
    levels = stats.open_by_level(db, user)
    cells = stats.heatmap(db, user)
    nl, ns = scale_size(db, "likelihood"), scale_size(db, "severity")
    grid = []
    for s in range(ns, 0, -1):                      # แถวบน = ความรุนแรงสูงสุด
        row = []
        for l in range(1, nl + 1):
            level = level_for_score(db, l * s)
            row.append({"l": l, "s": s, "score": l * s, "color": level["color"] if level else "#444",
                        "count": cells.get((l, s), 0)})
        grid.append(row)
    trend = stats.monthly_trend(db, user)
    peak = max([1] + [max(r, c) for _, r, c in trend])
    return render_template(
        "overview.html", counts=counts, status_th=STATUS_TH, open_total=counts["reported"] + counts["assessed"] + counts["assigned"],
        overdue=stats.overdue_count(db, user), levels=levels, level_peak=max([1] + [n for _, _, n in levels]),
        grid=grid, likelihoods=range(1, nl + 1), trend=trend, trend_peak=peak)


@bp.route("/overview/departments")
@permission_any(*ANY_HAZARD_PERM)
def overview_departments():
    return render_template("overview_departments.html", rows=stats.by_department(get_db(), g.user))
