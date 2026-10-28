"""safety/views/notifications.py — กระดิ่งและศูนย์แจ้งเตือนของฉัน (เห็น/แก้ได้เฉพาะแถวของตัวเอง; ของคนอื่นตอบ 404)"""
from __future__ import annotations

import math

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request

from ..db import get_db
from ..security import safety_login_required

bp = Blueprint("notifications", __name__)
PER_PAGE = 20


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def safe_internal(link) -> str | None:
    """ลิงก์ภายในเว็บเท่านั้น (ขึ้นต้นด้วย / ตัวเดียว ไม่มี \\ หรืออักขระควบคุม) — กัน open redirect"""
    if isinstance(link, str) and link.startswith("/") and not link.startswith("//") and "\\" not in link \
            and not any(ord(c) < 32 or ord(c) == 127 for c in link):
        return link
    return None


def _mine(notification_id: int):
    row = get_db().execute("SELECT * FROM notifications WHERE id=? AND user_id=?", (notification_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


def unread_count(db, user_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()[0]


@bp.route("/notifications")
@safety_login_required
def index():
    db = get_db()
    only_unread = request.args.get("unread") == "1"
    where = "user_id=?" + (" AND read_at IS NULL" if only_unread else "")
    total = db.execute(f"SELECT COUNT(*) FROM notifications WHERE {where}", (g.user["id"],)).fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(max(_int(request.args.get("page"), 1), 1), pages)
    rows = db.execute(f"SELECT * FROM notifications WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                      (g.user["id"], PER_PAGE, (page - 1) * PER_PAGE)).fetchall()
    return render_template("notifications.html", rows=rows, page=page, pages=pages, total=total, only_unread=only_unread)


@bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@safety_login_required
def mark_read(notification_id):
    _mine(notification_id)
    db = get_db()
    db.execute("UPDATE notifications SET read_at=COALESCE(read_at, datetime('now','localtime')) WHERE id=?", (notification_id,))
    db.commit()
    return redirect("/notifications")


@bp.route("/notifications/read-all", methods=["POST"])
@safety_login_required
def mark_all_read():
    db = get_db()
    db.execute("UPDATE notifications SET read_at=datetime('now','localtime') WHERE user_id=? AND read_at IS NULL", (g.user["id"],))
    db.commit()
    return redirect("/notifications")


@bp.route("/notifications/go/<int:notification_id>")
@safety_login_required
def go(notification_id):
    row = _mine(notification_id)
    db = get_db()
    db.execute("UPDATE notifications SET read_at=COALESCE(read_at, datetime('now','localtime')) WHERE id=?", (notification_id,))
    db.commit()
    return redirect(safe_internal(row["link"]) or "/notifications")


@bp.route("/notifications/unread-count")
@safety_login_required
def count():
    resp = jsonify({"count": unread_count(get_db(), g.user["id"])})
    resp.headers["Cache-Control"] = "no-store"
    return resp
