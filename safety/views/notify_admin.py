"""safety/views/notify_admin.py — ตั้งค่าการแจ้งเตือน (ผู้ดูแลระบบ): กฎ, ช่วงเวลาเงียบ/เวลางานประจำวัน, สถานะช่องทาง, คิวส่ง, เครื่องมือทดสอบ
ความลับ (โทเค็น/รหัสผ่าน/URL) อยู่ใน environment เท่านั้น หน้านี้แสดงเพียงว่า "ตั้งค่าแล้วหรือยัง" ไม่เคยแสดงค่า"""
from __future__ import annotations

import os
import re
import sqlite3
import time

from flask import Blueprint, flash, g, redirect, render_template, request

from .. import channel_config
from .. import digest, scheduler
from ..db import get_db
from ..notify import clean, valid_contact
from ..perms import permission_required
from ..schema import ACTIVE_CHANNELS, ACTIVE_OUTBOX, NOTIFY_EVENTS

bp = Blueprint("notify_admin", __name__, url_prefix="/admin")
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ROLE_RE = re.compile(r"^role:[a-z][a-z0-9_]{1,31}$")
EVENT_TH = {"hazard_reported": "แจ้งอันตรายใหม่", "hazard_assessed": "ประเมินความเสี่ยงแล้ว", "hazard_assigned": "มอบหมายผู้รับผิดชอบ",
            "hazard_closed": "ปิดรายการ", "hazard_rejected": "ปฏิเสธรายการ", "due_soon": "ใกล้ครบกำหนด", "overdue": "เกินกำหนด",
            "incident_reported": "แจ้งอุบัติเหตุใหม่", "incident_assigned": "มอบหมายผู้สอบสวน", "incident_closed": "ปิดรายการอุบัติเหตุ",
            "capa_assigned": "มอบหมาย CAPA", "capa_done": "CAPA ดำเนินการเสร็จ", "capa_verified": "CAPA ยืนยันผลแล้ว", "capa_reopened": "CAPA ถูกตีกลับ",
            "capa_due_soon": "CAPA ใกล้ครบกำหนด", "capa_overdue": "CAPA เกินกำหนด", "patrol_due": "ถึงรอบตรวจความปลอดภัย", "patrol_overdue": "เลยรอบตรวจความปลอดภัย",
            "report_due_soon": "ใกล้ครบกำหนดส่งรายงาน", "report_overdue": "เกินกำหนดส่งรายงาน", "user_requested": "มีคำขอสร้างบัญชีใหม่"}
SOON_EVENTS = ("due_soon", "capa_due_soon", "report_due_soon")                       # offset = จำนวนวันก่อนครบกำหนด (0-60)
LATE_EVENTS = ("overdue", "capa_overdue", "patrol_overdue", "report_overdue")         # offset = จำนวนวันหลังครบกำหนด (ตั้งแต่ 1)
CHANNEL_TH = {"inapp": "ในระบบ", "telegram": "Telegram", "line": "LINE", "teams": "Teams", "email": "อีเมล"}
STATUS_TH = {"pending": "รอส่ง", "failed": "ล้มเหลว (จะลองใหม่)", "dead": "ส่งไม่สำเร็จถาวร", "sent": "ส่งแล้ว", "cancelled": "ยกเลิก"}
MAX_RULES = 200


def _int(raw):
    try:
        return int(str(raw).strip()) if re.fullmatch(r"\s*-?\d{1,12}\s*", str(raw)) else None
    except (TypeError, ValueError):
        return None


def validate_rule(db, form, rule_id: int | None = None) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    event = form.get("event", "")
    if event not in NOTIFY_EVENTS:
        errors.append("เหตุการณ์ไม่ถูกต้อง")
    channels = [c for c in ACTIVE_CHANNELS if c in form.getlist("channels")]
    if not channels or any(c not in ACTIVE_CHANNELS for c in form.getlist("channels")):
        errors.append("เลือกช่องทางอย่างน้อย 1 ช่องทาง (ในระบบ/Teams/อีเมล)")
    roles = {r[0] for r in db.execute("SELECT key FROM safety_roles")}
    recipients: list[str] = []
    for item in (p.strip() for p in form.get("recipients", "").split(",")):
        if not item:
            continue
        if item in ("reporter", "owner") or (ROLE_RE.match(item) and item[5:] in roles) or (item.startswith("owner_else:") and ROLE_RE.match("role:" + item[11:]) and item[11:] in roles):
            if item not in recipients:
                recipients.append(item)
        else:
            errors.append(f"ผู้รับไม่ถูกต้อง: {clean(item, 40)} (ใช้ reporter, owner, owner_else:<บทบาทสำรอง> หรือ role:<รหัสบทบาทที่มีอยู่>)")
    if not recipients and channels != ["teams"]:
        errors.append("ต้องมีผู้รับอย่างน้อย 1 กลุ่ม (เว้นว่างได้เฉพาะเมื่อส่งเข้า Teams อย่างเดียว)")
    offset = _int(form.get("offset_days", "0"))
    if offset is None or not 0 <= offset <= 60:
        errors.append("จำนวนวัน (offset) ต้องเป็นจำนวนเต็ม 0-60")
    elif event in LATE_EVENTS and offset < 1:
        errors.append("กฎ 'เกินกำหนด' ต้องกำหนดจำนวนวันหลังครบกำหนดตั้งแต่ 1")
    elif event not in SOON_EVENTS + LATE_EVENTS and offset != 0:
        errors.append("เหตุการณ์นี้ไม่ใช้จำนวนวัน (ต้องเป็น 0)")
    min_raw = (form.get("min_score") or "").strip()
    min_score = None
    if min_raw:
        min_score = _int(min_raw)
        if min_score is None or min_score < 1 or min_score > 10000:
            errors.append("คะแนนขั้นต่ำต้องเป็นจำนวนเต็มตั้งแต่ 1")
        elif event != "hazard_assessed":
            errors.append("คะแนนขั้นต่ำใช้กับเหตุการณ์ 'ประเมินความเสี่ยงแล้ว' เท่านั้น")
    if errors:
        return None, errors
    clean_rule = {"event": event, "recipients": ",".join(recipients), "channels": ",".join(channels),
                  "offset_days": offset, "min_score": min_score}
    key = (event, frozenset(recipients), frozenset(channels), offset, min_score)
    for r in db.execute("SELECT * FROM notify_rules WHERE id IS NOT ?", (rule_id,)):
        if (r["event"], frozenset(p for p in r["recipients"].split(",") if p), frozenset(r["channels"].split(",")),
                r["offset_days"], r["min_score"]) == key:
            return None, ["มีกฎเดียวกันนี้อยู่แล้ว"]
    return clean_rule, []


def mask_target(channel: str, target: str) -> str:
    t = str(target or "")
    if channel == "teams":
        return "กลุ่ม Teams"
    if channel == "email" and "@" in t:
        local, _, domain = t.partition("@")
        return f"{clean(local[:1], 4)}***@{clean(domain, 80)}"
    return "***" + clean(t[-4 if channel == "line" else -3:], 4)


def channel_status(db=None) -> dict[str, str]:
    """สถานะช่องทางที่เปิดใช้ (อีเมล และ Teams) — แสดงเพียงว่าตั้งค่าแล้วหรือยัง ไม่แสดงค่า"""
    from ..channel_config import effective_env
    env = effective_env(db)
    teams = (env.get("TEAMS_WEBHOOK_URL") or "").strip()
    return {
        "teams": ("ช่อง Teams กลางตั้งค่าแล้ว" if teams.lower().startswith("https://") else "ช่อง Teams กลางต้องเป็น https (TEAMS_WEBHOOK_URL)") if teams
        else "ช่อง Teams กลางยังไม่ได้ตั้งค่า (TEAMS_WEBHOOK_URL) — แต่ผู้ใช้แต่ละคนใส่ Webhook ของตัวเองที่โปรไฟล์ได้",
        "email": "ตั้งค่าแล้ว" if env.get("SMTP_HOST") else "ยังไม่ได้ตั้งค่า (SMTP_HOST, SMTP_FROM)",
    }


def _rule_action(db, action: str, form) -> None:
    rule_id = _int(form.get("id"))
    if action == "rule_create":
        if db.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0] >= MAX_RULES:
            return flash(f"มีกฎครบ {MAX_RULES} ข้อแล้ว")
        rule, errors = validate_rule(db, form)
        if errors:
            return [flash(e) for e in errors] and None
        db.execute("INSERT INTO notify_rules(event, recipients, channels, offset_days, min_score) VALUES(?,?,?,?,?)",
                   (rule["event"], rule["recipients"], rule["channels"], rule["offset_days"], rule["min_score"]))
    else:
        row = db.execute("SELECT * FROM notify_rules WHERE id=?", (rule_id,)).fetchone() if rule_id is not None else None
        if row is None:
            return flash("ไม่พบกฎ")
        if action == "rule_toggle":
            db.execute("UPDATE notify_rules SET enabled = 1 - enabled WHERE id=?", (rule_id,))
        elif action == "rule_delete":
            db.execute("DELETE FROM notify_rules WHERE id=?", (rule_id,))
        else:
            rule, errors = validate_rule(db, form, rule_id)
            if errors:
                return [flash(e) for e in errors] and None
            db.execute("UPDATE notify_rules SET event=?, recipients=?, channels=?, offset_days=?, min_score=? WHERE id=?",
                       (rule["event"], rule["recipients"], rule["channels"], rule["offset_days"], rule["min_score"], rule_id))
    db.commit()


def _send_test(db) -> tuple[str, str]:
    """ส่งข้อความทดสอบถึงตัวเองทันทีทุกช่องทางที่เลือกไว้ในโปรไฟล์ แล้วรายงานผลจริง (สำเร็จ/ล้มเหลวพร้อมเหตุผล) — ผู้ดูแลกดเองครั้งละคำขอ จึงยอมให้รอผลสั้น ๆ"""
    from .. import delivery
    user, opted = g.user, set((g.user["notify_channels"] or "").split(","))
    stamp = f"test:{user['id']}:{time.time_ns()}"
    title, body = "ทดสอบการแจ้งเตือน AIAT Safety", "ข้อความทดสอบจากหน้าตั้งค่าการแจ้งเตือน — หากเห็นข้อความนี้แสดงว่าช่องทางทำงานปกติ"
    db.execute("INSERT INTO notifications(user_id, event, title, body, link, dedupe_key) VALUES(?,?,?,?,?,?)",
               (user["id"], "test", title, body, "/notifications", stamp))
    db.commit()
    from ..channel_config import effective_env
    env = {**effective_env(db), "NOTIFY_HTTP_TIMEOUT": "6"}
    ok, bad, names = [], [], {"email": "อีเมล", "teams": "Teams"}
    for channel in ACTIVE_OUTBOX:
        target = valid_contact(channel, user) if channel in opted else None
        if not target:
            continue
        try:
            delivery.REAL[channel](target, title, body, env)
            ok.append(names[channel])
        except delivery.TransportError as exc:
            bad.append(f"{names[channel]} ({exc})")
    if not ok and not bad:
        return ("ยังไม่มีช่องทางภายนอกที่ใช้ได้ — ไปที่ 'โปรไฟล์ของฉัน' กรอกอีเมลหรือ Teams และเลือกรับก่อน (ข้อความทดสอบเข้ากระดิ่งแล้ว)", "")
    msg = ("ส่งข้อความทดสอบสำเร็จ: " + ", ".join(ok) if ok else "") + (" · " if ok and bad else "") + ("ส่งไม่สำเร็จ: " + ", ".join(bad) if bad else "")
    return (msg, "celebrate" if ok and not bad else "")


@bp.route("/notifications", methods=["GET", "POST"])
@permission_required("users.manage")
def notifications():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action.startswith("rule_"):
            _rule_action(db, action, request.form)
        elif action == "channels_save":
            errors = channel_config.save(db, request.form)
            for e in errors:
                flash(e)
            if not errors:
                flash("บันทึกการตั้งค่าช่องทางแล้ว — กด «ส่งข้อความทดสอบถึงฉัน» เพื่อตรวจว่าส่งได้จริง", "celebrate")
                return redirect("/admin/notifications")
        elif action == "settings_save":
            values = {k: (request.form.get(k) or "").strip() for k in ("quiet_start", "quiet_end", "daily_time")}
            if all(HHMM_RE.match(v) for v in values.values()):
                db.executemany("INSERT INTO app_settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", values.items())
                db.commit()
            else:
                flash("เวลาต้องเป็นรูปแบบ HH:MM (00:00-23:59) ทั้ง 3 ช่อง")
        elif action == "digest_toggle":
            db.execute("INSERT INTO app_settings(key, value) VALUES('backlog_digest', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if request.form.get("on") == "1" else "0",))
            db.commit()
            flash("เปิดส่งสรุปงานค้างรายวันแล้ว" if request.form.get("on") == "1" else "ปิดส่งสรุปงานค้างรายวันแล้ว")
        elif action in ("outbox_retry", "outbox_cancel"):
            oid = _int(request.form.get("id"))
            if action == "outbox_retry":
                db.execute("UPDATE notify_outbox SET status='pending', attempts=0, last_error='', next_try_at=datetime('now','localtime') "
                           "WHERE id=? AND status IN ('dead','failed')", (oid,))
            else:
                db.execute("UPDATE notify_outbox SET status='cancelled' WHERE id=? AND status IN ('pending','failed')", (oid,))
            db.commit()
        elif action == "send_test":
            msg, cat = _send_test(db)
            flash(msg, cat) if cat else flash(msg)
        elif action == "run_daily_now":
            res = scheduler.run_daily(db, force=True)
            flash(f"รันงานประจำวันแล้ว: แจ้งเตือนใหม่ {res['notifications']} รายการ, เข้าคิวภายนอก {res['outbox']}, ข้อผิดพลาด {res['errors']}"
                  f"{', สำรองข้อมูลแล้ว' if res['backup'] else ', (สำรองข้อมูลไม่สำเร็จ)'}")
        return redirect("/admin/notifications")
    settings = {r[0]: r[1] for r in db.execute("SELECT key, value FROM app_settings")}
    rule_rows = db.execute("SELECT * FROM notify_rules ORDER BY event, offset_days, id").fetchall()
    counts = {r[0]: r[1] for r in db.execute("SELECT status, COUNT(*) FROM notify_outbox GROUP BY status")}
    pending = [dict(r, shown=mask_target(r["channel"], r["target"])) for r in db.execute(
        "SELECT * FROM notify_outbox WHERE status <> 'sent' ORDER BY id DESC LIMIT 50")]
    return render_template(
        "notify_admin.html", rules=rule_rows, settings=settings, counts=counts, outbox=pending, status=channel_status(db), chan=channel_config.form_view(db), tls_modes=channel_config.TLS_MODES,
        event_th=EVENT_TH, channel_th=CHANNEL_TH, status_th=STATUS_TH, channels=ACTIVE_CHANNELS, events=NOTIFY_EVENTS,
        roles=db.execute("SELECT key FROM safety_roles ORDER BY key").fetchall(),
        no_enabled=not any(r["enabled"] for r in rule_rows), digest_on=digest.enabled(db))
