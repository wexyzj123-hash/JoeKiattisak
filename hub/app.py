"""hub/app.py — เว็บกลาง (Hub): ล็อกอิน + หน้ารวมระบบ + ออก SSO token"""
from __future__ import annotations

import hashlib
import os

from flask import Flask, g

from shared.envfile import validate_secrets

from . import db as hubdb
from .security import install_csrf, load_user

ROOT = os.path.dirname(os.path.abspath(__file__))


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("HUB_DATABASE", os.path.join(ROOT, "data", "hub.db")),
        SSO_SECRET=os.environ.get("SSO_SECRET", ""),
        SERVICE_TOKEN=os.environ.get("SERVICE_TOKEN", ""),
        SAFETY_URL=os.environ.get("SAFETY_URL", "http://localhost:5200"),
        SESSION_COOKIE_NAME="aiat_hub_session", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    validate_secrets(app.config["SSO_SECRET"], app.config["SERVICE_TOKEN"])
    app.config.setdefault("SECRET_KEY", os.environ.get("HUB_SECRET_KEY"))
    if not app.config["SECRET_KEY"]:      # แนะนำให้ตั้ง HUB_SECRET_KEY แยกจาก SSO_SECRET (ผู้ถือ SSO_SECRET จะปลอมคุกกี้ไม่ได้)
        app.config["SECRET_KEY"] = hashlib.sha256(("hub-session:" + app.config["SSO_SECRET"]).encode()).hexdigest()

    hubdb.init_app(app)
    install_csrf(app)
    app.before_request(load_user)

    from .views import admin, auth, launcher
    app.register_blueprint(admin.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(launcher.bp)
    return app
