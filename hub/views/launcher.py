"""hub/views/launcher.py — หน้ารวมระบบ + ส่งผู้ใช้ไปยังแต่ละระบบพร้อม SSO token"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, send_file

from shared.sso import sign_token
from shared.urls import public_url

from ..db import get_db
from ..security import login_required

bp = Blueprint("launcher", __name__)
THEME_CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "shared", "theme.css")
THEME_JS = THEME_CSS[:-len("theme.css")] + "theme.js"


BADGE_APPS = {"safety"}          # ระบบที่มี /api/summary ให้ Hub ดึงตัวเลข


def fetch_badge(app_row, service_token: str, timeout: float = 2.0) -> int | None:
    """จำนวนรายการที่ต้องสนใจของระบบนั้น; ทุกความผิดพลาด (ล่ม/ช้า/รูปแบบผิด) → None เพื่อไม่ให้หน้า Hub พัง"""
    try:
        req = Request(app_row["url"].rstrip("/") + "/api/summary", headers={"X-Service-Token": service_token})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        return int(data["open_hazards"]) + int(data["overdue"])
    except Exception:       # noqa: BLE001 — badge เป็นของเสริม ห้ามทำให้หน้าแรกล้ม
        return None


def _allowed(app_row) -> bool:
    return app_row["required_role"] in ("", g.user["role"])


@bp.route("/static/theme.js")
def theme_js():
    return send_file(THEME_JS, mimetype="text/javascript", max_age=3600)


@bp.route("/static/theme.css")
def theme_css():
    return send_file(THEME_CSS, mimetype="text/css", max_age=3600)


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
@login_required
def home():
    rows = get_db().execute("SELECT * FROM app_registry WHERE enabled=1 ORDER BY sort, id").fetchall()
    apps = []
    for r in rows:
        if _allowed(r):
            badge = fetch_badge(r, current_app.config["SERVICE_TOKEN"]) if r["key"] in BADGE_APPS else None
            apps.append({"key": r["key"], "name_th": r["name_th"], "badge": badge, "link": r["link_mode"] == "link"})
    return render_template("launcher.html", apps=apps, any_badge=any(a["badge"] for a in apps))


@bp.route("/go/<key>")
@login_required
def go(key: str):
    row = get_db().execute("SELECT * FROM app_registry WHERE key=? AND enabled=1", (key,)).fetchone()
    if row is None:
        abort(404)
    if not _allowed(row):
        abort(403)
    if row["link_mode"] == "link":                      # ระบบเดิมที่ไม่มี SSO: เปิดที่อยู่ตรง ๆ ห้ามส่ง token ไปให้
        return redirect(public_url(row["url"], request.host))
    token = sign_token(current_app.config["SSO_SECRET"],
                       {"uid": g.user["id"], "name": g.user["name_th"], "roles": [g.user["role"]], "aud": row["key"]})
    return redirect(public_url(row["url"].rstrip("/"), request.host) + "/sso?" + urlencode({"token": token}))
