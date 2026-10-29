"""hub/views/launcher.py — หน้ารวมระบบ + ส่งผู้ใช้ไปยังแต่ละระบบพร้อม SSO token"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, send_file, url_for

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
    role = g.user["role"] if g.get("user") else None          # ยังไม่ล็อกอิน: เห็นเฉพาะระบบที่ไม่จำกัดบทบาท
    return app_row["required_role"] == "" or app_row["required_role"] == role


@bp.route("/static/theme.js")
def theme_js():
    return send_file(THEME_JS, mimetype="text/javascript", max_age=3600)


@bp.route("/static/theme.css")
def theme_css():
    return send_file(THEME_CSS, mimetype="text/css", max_age=3600)


ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


HUB_ASSETS = {"scene.css": "text/css", "login.js": "text/javascript", "gate.js": "text/javascript", "register.js": "text/javascript", "logout.js": "text/javascript"}


@bp.route("/static/hub/<name>")
def hub_asset(name):
    if name not in HUB_ASSETS:
        abort(404)
    return send_file(os.path.join(ASSETS, name), mimetype=HUB_ASSETS[name], max_age=300)


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
def home():
    """หน้าแรกของ Hub: ดูรายการระบบได้โดยไม่ต้องล็อกอิน — กดเข้าระบบที่ต้องยืนยันตัวตน (เช่น Safety) แล้วจึงถามรหัสผ่าน"""
    rows = get_db().execute("SELECT * FROM app_registry WHERE enabled=1 ORDER BY sort, id").fetchall()
    apps = []
    for r in rows:
        if _allowed(r):
            badge = fetch_badge(r, current_app.config["SERVICE_TOKEN"]) if g.user and r["key"] in BADGE_APPS else None   # ตัวเลขภายในเห็นเฉพาะผู้ล็อกอิน
            apps.append({"key": r["key"], "name_th": r["name_th"], "badge": badge, "link": r["link_mode"] == "link"})
    pending = get_db().execute("SELECT COUNT(*) FROM users WHERE pending=1").fetchone()[0] if g.user and g.user["role"] == "admin" else 0
    return render_template("launcher.html", apps=apps, any_badge=any(a["badge"] for a in apps), pending=pending)


@bp.route("/go/<key>")
def go(key: str):
    row = get_db().execute("SELECT * FROM app_registry WHERE key=? AND enabled=1", (key,)).fetchone()
    if row is None:
        abort(404)
    if row["link_mode"] == "link":                      # ระบบเดิมที่ไม่มี SSO: เปิดที่อยู่ตรง ๆ (มีหน้าล็อกอินของตัวเอง) ห้ามส่ง token ไปให้
        if not _allowed(row):
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?"))) if g.user is None else abort(403)
        return redirect(public_url(row["url"], request.host))
    if g.user is None:                                  # ระบบที่ใช้บัญชีของ Hub: ต้องล็อกอินก่อน แล้วกลับมาที่นี่เพื่อเข้าระบบต่อ
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    if not _allowed(row):
        abort(403)
    token = sign_token(current_app.config["SSO_SECRET"],
                       {"uid": g.user["id"], "name": g.user["name_th"], "roles": [g.user["role"]], "aud": row["key"],
                        "org": "|".join(g.user[k] for k in ("req_factory", "req_department", "req_line") if g.user[k])})
    return redirect(public_url(row["url"].rstrip("/"), request.host) + "/sso?" + urlencode({"token": token}))
