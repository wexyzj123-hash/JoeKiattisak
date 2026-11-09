"""safety/views/profile.py — โปรไฟล์ของฉัน: ช่องทางติดต่อสำหรับแจ้งเตือนนอกระบบ (อีเมล / Microsoft Teams) และการเปลี่ยนรหัสผ่านของตัวเอง"""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request

from shared.auth import MIN_PASSWORD, check_password, hash_password

from ..db import get_db
from ..notify import teams_host_ok, valid_contact
from ..security import safety_login_required

bp = Blueprint("profile", __name__)
USER_CHANNELS = (("email", "email", "อีเมล"), ("teams", "teams_webhook", "Microsoft Teams (Webhook URL)"))


@bp.route("/profile", methods=["GET", "POST"])
@safety_login_required
def profile():
    db, errors = get_db(), []
    values = dict(g.user)
    chosen = set((g.user["notify_channels"] or "inapp").split(","))
    if request.method == "POST" and request.form.get("action") == "password":
        old, new = request.form.get("old_password", ""), request.form.get("new_password", "")
        if not check_password(g.user["password_hash"], old):
            errors.append("รหัสผ่านปัจจุบันไม่ถูกต้อง")
        elif len(new) < MIN_PASSWORD:
            errors.append(f"รหัสผ่านใหม่ต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")
        elif new != request.form.get("new_password2", ""):
            errors.append("รหัสผ่านใหม่สองช่องไม่ตรงกัน")
        else:
            db.execute("UPDATE safety_users SET password_hash=? WHERE id=?", (hash_password(new), g.user["id"]))
            db.commit()
            flash("เปลี่ยนรหัสผ่านเรียบร้อยแล้ว", "celebrate")
            return redirect("/profile")
    elif request.method == "POST":
        values = {"email": "", "teams_webhook": ""}
        chosen = set()
        for channel, field, label in USER_CHANNELS:
            raw = (request.form.get(field) or "").strip()
            values[field] = raw
            if raw and valid_contact(channel, {field: raw}) is None:
                errors.append(f"{label} ไม่ถูกต้อง" + (" — ต้องเป็นลิงก์ https ของ Microsoft (webhook.office.com / logic.azure.com ฯลฯ)" if channel == "teams" else ""))
            if channel in request.form.getlist("channels"):
                chosen.add(channel)
                if not raw:
                    errors.append(f"กรุณากรอกข้อมูลติดต่อ {label} ก่อนเลือกรับแจ้งเตือนทางช่องทางนี้")
        if not errors:
            channels = ",".join(["inapp"] + [c for c, _, _ in USER_CHANNELS if c in chosen])
            db.execute("UPDATE safety_users SET email=?, teams_webhook=?, notify_channels=? WHERE id=?",
                       (values["email"] or None, values["teams_webhook"] or None, channels, g.user["id"]))
            db.commit()
            got = [label.split(" (")[0] for c, _, label in USER_CHANNELS if c in chosen]
            flash("บันทึกข้อมูลติดต่อเรียบร้อยแล้ว" + (f" — จะได้รับแจ้งเตือนงานค้างและการติดตามทาง {' และ '.join(got)} (นอกเหนือจากกระดิ่งในระบบ)" if got else " — รับแจ้งเตือนทางกระดิ่งในระบบอย่างเดียว"), "celebrate")
            return redirect("/profile")
    return render_template("profile.html", values=values, chosen=chosen, errors=errors, channels=USER_CHANNELS, native=bool(g.user["password_hash"]))
