"""safety/views/home.py — หน้าแรก (placeholder ของเฟส 1) และออกจากระบบ"""
from __future__ import annotations

import os

from flask import Blueprint, current_app, g, redirect, render_template, send_file, session

from .. import charts, stats
from ..db import get_db
from ..hazards import STATUS_TH
from ..perms import user_permissions
from ..security import safety_login_required

bp = Blueprint("home", __name__)
THEME_CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "shared", "theme.css")
THEME_JS = THEME_CSS[:-len("theme.css")] + "theme.js"


@bp.route("/static/theme.js")
def theme_js():
    return send_file(THEME_JS, mimetype="text/javascript", max_age=3600)


@bp.route("/static/theme.css")
def theme_css():
    return send_file(THEME_CSS, mimetype="text/css", max_age=3600)


@bp.route("/")
@safety_login_required
def index():
    db, user = get_db(), g.user
    counts = stats.counts_by_status(db, user)
    donut_svg, legend = charts.donut(stats.open_by_type(db, user))
    trend_rows = stats.monthly_trend(db, user)
    latest_notes = db.execute("SELECT id, title, link, created_at, read_at FROM notifications WHERE user_id = ? "
                              "ORDER BY created_at DESC, id DESC LIMIT 5", (user["id"],)).fetchall()
    return render_template(
        "home.html", open_total=counts["reported"] + counts["assessed"] + counts["assigned"], overdue=stats.overdue_count(db, user),
        closed_month=stats.closed_this_month(db, user), high_risk=stats.high_risk_open(db, user),
        donut=donut_svg, legend=legend, trend_svg=charts.trend(trend_rows), trend_rows=trend_rows,
        latest=stats.latest_hazards(db, user), status_th=STATUS_TH, notes=latest_notes,
        can_report="hazard.report" in user_permissions(db, user))


@bp.route("/signout")
def signout():
    """Hub เรียกหลังผู้ใช้ออกจากระบบกลาง — ล้าง session ของ Safety แล้วกลับหน้าล็อกอินของ Hub"""
    session.clear()
    return redirect(current_app.config["HUB_URL"].rstrip("/") + "/login")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(current_app.config["HUB_URL"].rstrip("/") + "/logout")
