"""hub/db.py — SQLite (1 connection ต่อ request) ใช้แบบเดียวกับ ecs/db.py"""
from __future__ import annotations

import os
import sqlite3

from flask import current_app, g


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app) -> None:
    from .schema import ensure_schema, seed_default_apps, seed_link_apps
    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    con = connect(app.config["DATABASE"])
    try:
        ensure_schema(con)
        seed_default_apps(con, app.config.get("SAFETY_URL", "http://localhost:5200"))
        seed_link_apps(con, app.config.get("LINK_APPS") if app.config.get("LINK_APPS") is not None else os.environ)
    finally:
        con.close()
    app.teardown_appcontext(close_db)
