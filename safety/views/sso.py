"""safety/views/sso.py — รับ token จาก Hub: ตรวจลายเซ็น/อายุ, ใช้ nonce ได้ครั้งเดียว, สร้างผู้ใช้ของ Safety"""
from __future__ import annotations

import sqlite3
import time

from flask import Blueprint, current_app, redirect, render_template, request, session

from shared.sso import SsoError, verify_token

from ..db import get_db
from ..urls import hub_public

bp = Blueprint("sso", __name__)


def _deny():
    return render_template("sso_error.html", hub_url=hub_public()), 403


@bp.route("/sso")
def sso():
    try:
        claims = verify_token(current_app.config["SSO_SECRET"], request.args.get("token", ""))
        hub_uid = int(claims["uid"])
        if claims.get("aud") != "safety":      # token ที่ออกให้ระบบอื่นห้ามใช้ที่นี่
            return _deny()
    except (SsoError, KeyError, TypeError, ValueError):
        return _deny()
    db = get_db()
    db.execute("DELETE FROM sso_nonces WHERE exp < ?", (time.time(),))
    try:
        db.execute("INSERT INTO sso_nonces(nonce, exp) VALUES(?,?)", (claims["nonce"], float(claims["exp"]) + 60))
    except sqlite3.IntegrityError:          # token เดิมถูกใช้ไปแล้ว (replay)
        db.rollback()
        return _deny()
    role = "admin" if "admin" in (claims.get("roles") or []) else "reporter"   # ใช้ตอนสร้างครั้งแรกเท่านั้น
    org = " › ".join(p.strip()[:100] for p in str(claims.get("org", "")).split("|") if p.strip())[:300]
    db.execute("INSERT OR IGNORE INTO safety_users(hub_uid, name, role, requested_org) VALUES(?,?,?,?)",
               (hub_uid, str(claims.get("name", "")), role, org))
    user = db.execute("SELECT * FROM safety_users WHERE hub_uid=?", (hub_uid,)).fetchone()
    if not user["active"]:
        db.commit()
        return _deny()
    if org and not user["requested_org"]:
        db.execute("UPDATE safety_users SET requested_org=? WHERE id=?", (org, user["id"]))
    if user["name"] != claims.get("name", user["name"]):
        db.execute("UPDATE safety_users SET name=? WHERE id=?", (str(claims["name"]), user["id"]))
    db.commit()
    session.clear()
    session.permanent = True            # อายุตาม PERMANENT_SESSION_LIFETIME (8 ชั่วโมง)
    session["suid"] = user["id"]
    return redirect("/")
