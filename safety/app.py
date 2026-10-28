"""safety/app.py — ระบบความปลอดภัย (Safety): เข้าสู่ระบบผ่าน SSO ของ Hub"""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta

from flask import Flask, g, request

from shared.envfile import validate_secrets

from . import db as safetydb
from .security import install_csrf, load_user
from .urls import hub_public

ROOT = os.path.dirname(os.path.abspath(__file__))


def create_app(test_config: dict | None = None) -> Flask:
    from shared.timezone import apply_timezone
    apply_timezone()                                # เวลาไทยทั้งโปรเซส (datetime.now / SQLite localtime)
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("SAFETY_DATABASE", os.path.join(ROOT, "data", "safety.db")),
        SSO_SECRET=os.environ.get("SSO_SECRET", ""),
        SERVICE_TOKEN=os.environ.get("SERVICE_TOKEN", ""),
        HUB_URL=os.environ.get("HUB_URL", "http://localhost:5000"),
        SESSION_COOKIE_NAME="aiat_safety_session", PERMANENT_SESSION_LIFETIME=timedelta(hours=8), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    )
    app.config.setdefault("URL_PREFIX", os.environ.get("SAFETY_PREFIX", ""))      # path นำหน้าเมื่ออยู่หลังพร็อกซีที่ใช้พอร์ตร่วมกับระบบอื่น
    if test_config:
        app.config.update(test_config)
    validate_secrets(app.config["SSO_SECRET"], app.config["SERVICE_TOKEN"])
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = os.environ.get("SAFETY_SECRET_KEY") or None
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = hashlib.sha256(("safety-session:" + app.config["SSO_SECRET"]).encode()).hexdigest()

    app.config.setdefault("UPLOAD_ROOT", os.environ.get("SAFETY_UPLOAD_ROOT", os.path.join(ROOT, "data", "uploads")))
    if app.config.get("MAX_CONTENT_LENGTH") is None:           # Flask ตั้งคีย์นี้เป็น None ไว้แล้ว — setdefault ไม่ทำงาน
        app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    safetydb.init_app(app)
    from markupsafe import Markup
    from shared.mascot import DEFAULT_LABEL, mascot_markup
    app.jinja_env.globals["mascot"] = lambda mood="default", size=120, label=DEFAULT_LABEL, decorative=False: Markup(mascot_markup(mood, size, label, decorative))
    from shared.icons import icon
    from shared import brand
    app.jinja_env.globals.update(aisin_wordmark=lambda *a, **k: Markup(brand.aisin_wordmark(*a, **k)), shield_logo=lambda *a, **k: Markup(brand.shield_logo(*a, **k)),
                                 BRAND_TITLE=brand.TITLE, BRAND_TAGLINE=brand.TAGLINE, BRAND_FOOTER=brand.FOOTER)
    app.jinja_env.globals["icon"] = icon
    install_csrf(app)
    app.before_request(load_user)
    if app.config.get("HUB_STATUS_CHECK") is None:
        app.config["HUB_STATUS_CHECK"] = not app.config.get("TESTING")      # ทดสอบปิดไว้ (ไม่ให้เรียกเครือข่าย) เว้นแต่ตั้งใจเปิด

    @app.before_request
    def _hub_status():
        """ผู้ใช้ที่ถูกปิดที่ Hub ต้องหลุดจาก Safety (ตรวจแบบจำผลไว้ — ดู hubcheck.py)"""
        if not app.config["HUB_STATUS_CHECK"] or not app.config["SERVICE_TOKEN"] or g.get("user") is None:
            return None
        if request.path.startswith(("/sso", "/api/", "/static/", "/signout")):
            return None
        from flask import redirect, session
        from .hubcheck import hub_user_active
        if not hub_user_active(app, g.user["hub_uid"]):
            session.clear()
            return redirect(hub_public())
        return None

    from .views import (api, criteria, custom_fields, hazards, home, master, notifications, notify_admin, overview, pages, profile, search, injury_admin, incidents, patrol_admin, patrols, capas, drawer, report_due, reports,
                        roles, sso, users)
    app.register_blueprint(notify_admin.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(injury_admin.bp)
    app.register_blueprint(incidents.bp)
    app.register_blueprint(patrol_admin.bp)
    app.register_blueprint(patrols.bp)
    app.register_blueprint(capas.bp)
    app.register_blueprint(drawer.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(report_due.bp)
    app.register_blueprint(notifications.bp)
    app.register_blueprint(profile.bp)
    app.register_blueprint(overview.bp)
    app.register_blueprint(hazards.bp)
    app.register_blueprint(custom_fields.bp)
    app.register_blueprint(criteria.bp)
    app.register_blueprint(pages.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(master.bp)
    app.register_blueprint(roles.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(sso.bp)
    app.register_blueprint(home.bp)

    @app.errorhandler(413)
    def _too_large(_exc):
        from flask import render_template
        return render_template("too_large.html"), 413

    @app.context_processor
    def _nav():
        if g.get("user") is None:
            return {}
        from .db import get_db
        from .nav import nav_items
        from .perms import user_permissions
        from .views.notifications import unread_count
        db = get_db()
        role = db.execute("SELECT name_th FROM safety_roles WHERE key = ?", (g.user["role"],)).fetchone()
        return {"nav": nav_items(user_permissions(db, g.user), request.path), "unread_count": unread_count(db, g.user["id"]),
                "role_name": role["name_th"] if role else g.user["role"]}

    from shared.prefix import install_prefix
    install_prefix(app, app.config.get("URL_PREFIX"))
    return app
