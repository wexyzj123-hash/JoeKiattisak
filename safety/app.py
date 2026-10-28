"""safety/app.py — ระบบความปลอดภัย (Safety): เข้าสู่ระบบผ่าน SSO ของ Hub"""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta

from flask import Flask, g, request

from shared.envfile import validate_secrets

from . import db as safetydb
from .security import install_csrf, load_user

ROOT = os.path.dirname(os.path.abspath(__file__))


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("SAFETY_DATABASE", os.path.join(ROOT, "data", "safety.db")),
        SSO_SECRET=os.environ.get("SSO_SECRET", ""),
        SERVICE_TOKEN=os.environ.get("SERVICE_TOKEN", ""),
        HUB_URL=os.environ.get("HUB_URL", "http://localhost:5000"),
        SESSION_COOKIE_NAME="aiat_safety_session", PERMANENT_SESSION_LIFETIME=timedelta(hours=8), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    validate_secrets(app.config["SSO_SECRET"], app.config["SERVICE_TOKEN"])
    app.config.setdefault("SECRET_KEY", os.environ.get("SAFETY_SECRET_KEY"))
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = hashlib.sha256(("safety-session:" + app.config["SSO_SECRET"]).encode()).hexdigest()

    app.config.setdefault("UPLOAD_ROOT", os.environ.get("SAFETY_UPLOAD_ROOT", os.path.join(ROOT, "data", "uploads")))
    app.config.setdefault("MAX_CONTENT_LENGTH", 25 * 1024 * 1024)
    safetydb.init_app(app)
    install_csrf(app)
    app.before_request(load_user)

    from .views import api, criteria, custom_fields, hazards, home, master, pages, roles, sso, users
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

    @app.context_processor
    def _nav():
        if g.get("user") is None:
            return {}
        from .db import get_db
        from .nav import nav_items
        from .perms import user_permissions
        return {"nav": nav_items(user_permissions(get_db(), g.user), request.path)}

    return app
