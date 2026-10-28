"""hub/views/launcher.py — หน้ารวมระบบ + ส่งผู้ใช้ไปยังแต่ละระบบพร้อม SSO token"""
from __future__ import annotations

import os
from urllib.parse import urlencode

from flask import Blueprint, abort, current_app, g, redirect, render_template, send_file

from shared.sso import sign_token

from ..db import get_db
from ..security import login_required

bp = Blueprint("launcher", __name__)
THEME_CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "shared", "theme.css")


def _allowed(app_row) -> bool:
    return app_row["required_role"] in ("", g.user["role"])


@bp.route("/static/theme.css")
def theme_css():
    return send_file(THEME_CSS, mimetype="text/css", max_age=3600)


@bp.route("/")
@login_required
def home():
    rows = get_db().execute("SELECT * FROM app_registry WHERE enabled=1 ORDER BY sort, id").fetchall()
    return render_template("launcher.html", apps=[r for r in rows if _allowed(r)])


@bp.route("/go/<key>")
@login_required
def go(key: str):
    row = get_db().execute("SELECT * FROM app_registry WHERE key=? AND enabled=1", (key,)).fetchone()
    if row is None:
        abort(404)
    if not _allowed(row):
        abort(403)
    token = sign_token(current_app.config["SSO_SECRET"],
                       {"uid": g.user["id"], "name": g.user["name_th"], "roles": [g.user["role"]]})
    return redirect(row["url"].rstrip("/") + "/sso?" + urlencode({"token": token}))
