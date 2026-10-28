"""safety/app.py — ระบบความปลอดภัย (Safety): เข้าสู่ระบบผ่าน SSO ของ Hub"""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta

from flask import Flask

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

    safetydb.init_app(app)
    install_csrf(app)
    app.before_request(load_user)

    from .views import api, home, sso
    app.register_blueprint(api.bp)
    app.register_blueprint(sso.bp)
    app.register_blueprint(home.bp)
    return app
