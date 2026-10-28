"""safety/views/home.py — หน้าแรก (placeholder ของเฟส 1) และออกจากระบบ"""
from __future__ import annotations

import os

from flask import Blueprint, current_app, redirect, render_template, send_file, session

from ..security import safety_login_required

bp = Blueprint("home", __name__)
THEME_CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "shared", "theme.css")


@bp.route("/static/theme.css")
def theme_css():
    return send_file(THEME_CSS, mimetype="text/css", max_age=3600)


@bp.route("/")
@safety_login_required
def index():
    return render_template("home.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(current_app.config["HUB_URL"].rstrip("/") + "/logout")
