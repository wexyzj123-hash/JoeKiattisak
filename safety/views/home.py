"""safety/views/home.py — หน้าแรก (placeholder ของเฟส 1) และออกจากระบบ"""
from __future__ import annotations

import os

from flask import abort, Blueprint, current_app, g, redirect, render_template, send_file, session

from .. import capa, charts, dash, incident_stats, patrol, stats
from .. import timeutil
from ..db import get_db
from ..hazards import STATUS_TH
from ..perms import user_permissions
from ..security import safety_login_required
from ..urls import hub_public

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


SAFETY_ASSETS = {"premium.css": "text/css", "premium.js": "text/javascript"}


@bp.route("/static/safety/<name>")
def safety_asset(name):
    if name not in SAFETY_ASSETS:
        abort(404)
    return send_file(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", name), mimetype=SAFETY_ASSETS[name], max_age=300)


@bp.route("/static/mascot/<name>")
def mascot_file(name):
    """ภาพมาสคอตจริง (ถ้ามี) — เฉพาะชื่อท่าที่รู้จักและนามสกุล png/webp เท่านั้น"""
    from shared import mascot
    stem, dot, ext = name.rpartition(".")
    if stem not in mascot.MOODS or "." + ext not in mascot.IMAGE_EXTS:
        abort(404)
    f = mascot.image_file(stem)
    if f is None or f.name != name:
        abort(404)
    return send_file(f, mimetype=mascot.IMAGE_EXTS["." + ext], max_age=3600)


@bp.route("/static/brand/<name>")
def brand_file(name):
    from shared import brand
    if name not in brand.LOGO_FILES or not (brand.ASSET_DIR / name).is_file():
        abort(404)
    return send_file(brand.ASSET_DIR / name, mimetype="image/png", max_age=3600)


@bp.route("/")
@safety_login_required
def index():
    db, user = get_db(), g.user
    counts = stats.counts_by_status(db, user)
    donut_svg, legend = charts.donut(stats.open_by_type(db, user))
    trend_rows = stats.monthly_trend(db, user)
    latest_notes = db.execute("SELECT id, title, link, created_at, read_at FROM notifications WHERE user_id = ? "
                              "ORDER BY created_at DESC, id DESC LIMIT 5", (user["id"],)).fetchall()
    lti_days, lti_date = incident_stats.days_without_lost_time(db, user)
    perms = user_permissions(db, user)
    dd = dash.home_data(db, user)
    return render_template(
        "home.html", dd=dd, spark=charts.spark, this_month=timeutil.today().strftime("%Y-%m"), lti_days=lti_days, lti_date=lti_date, capa_overdue=capa.overdue_count(db, user), latest_patrols=patrol.latest_rounds(db, user, 5),
        can_patrol=bool(perms & {"patrol.perform", "patrol.view"}), can_capa=bool(perms & {"capa.create", "capa.verify", "hazard.report", "incident.report", "patrol.perform"}), open_total=counts["reported"] + counts["assessed"] + counts["assigned"], overdue=stats.overdue_count(db, user),
        closed_month=stats.closed_this_month(db, user), high_risk=stats.high_risk_open(db, user),
        donut=donut_svg, legend=legend, trend_svg=charts.trend(trend_rows), trend_rows=trend_rows,
        latest=stats.latest_hazards(db, user), status_th=STATUS_TH, notes=latest_notes,
        can_report="hazard.report" in user_permissions(db, user),
        can_list=bool(user_permissions(db, user) & {"hazard.report", "hazard.assess", "hazard.assign", "hazard.close", "master.view"}))


@bp.route("/signout")
def signout():
    """Hub เรียกหลังผู้ใช้ออกจากระบบกลาง — ล้าง session ของ Safety แล้วกลับหน้าล็อกอินของ Hub"""
    session.clear()
    return redirect(hub_public("/login"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(hub_public("/logout"))
