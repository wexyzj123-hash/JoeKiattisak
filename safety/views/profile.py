"""safety/views/profile.py — โปรไฟล์ของฉัน: ช่องทางติดต่อสำหรับแจ้งเตือนภายนอก (Telegram / LINE / อีเมล) และการเลือกรับ"""
from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, request

from ..db import get_db
from ..notify import valid_contact
from ..security import safety_login_required

bp = Blueprint("profile", __name__)
USER_CHANNELS = (("telegram", "telegram_chat_id", "Telegram (chat id)"), ("line", "line_user_id", "LINE (user id)"),
                 ("email", "email", "อีเมล"))


@bp.route("/profile", methods=["GET", "POST"])
@safety_login_required
def profile():
    db, errors = get_db(), []
    values = dict(g.user)
    chosen = set((g.user["notify_channels"] or "inapp").split(","))
    if request.method == "POST":
        values = {"telegram_chat_id": "", "line_user_id": "", "email": ""}
        chosen = set()
        for channel, field, label in USER_CHANNELS:
            raw = (request.form.get(field) or "").strip()
            values[field] = raw
            if raw and valid_contact(channel, {field: raw}) is None:
                errors.append(f"{label} ไม่ถูกต้อง")
            if channel in request.form.getlist("channels"):
                chosen.add(channel)
                if not raw:
                    errors.append(f"กรุณากรอกข้อมูลติดต่อ {label} ก่อนเลือกรับแจ้งเตือนทางช่องทางนี้")
        if not errors:
            channels = ",".join(["inapp"] + [c for c, _, _ in USER_CHANNELS if c in chosen])
            db.execute("UPDATE safety_users SET telegram_chat_id=?, line_user_id=?, email=?, notify_channels=? WHERE id=?",
                       (values["telegram_chat_id"] or None, values["line_user_id"] or None, values["email"] or None,
                        channels, g.user["id"]))
            db.commit()
            return redirect("/profile")
    return render_template("profile.html", values=values, chosen=chosen, errors=errors, channels=USER_CHANNELS)
