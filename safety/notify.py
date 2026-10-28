"""safety/notify.py — ศูนย์กลางการแจ้งเตือน: ใครต้องได้รับ (ตามกฎใน notify_rules), ข้อความ, และการเขียนแถวแจ้งเตือน/คิวส่ง
ทุกอย่างเขียนลงฐานข้อมูลใน transaction ของผู้เรียก (ไม่ commit, ไม่เรียกเครือข่าย) — การส่งจริงเป็นหน้าที่ของ delivery.py
ปัญหาในการแจ้งเตือนต้องไม่ทำให้ขั้นตอนหลักล้ม: emit() ครอบด้วย SAVEPOINT และกลืนข้อผิดพลาด (บันทึก log)"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Mapping

from .hazards import user_in_scope
from .perms import user_permissions
from .schema import NOTIFY_EVENTS, OUTBOX_CHANNELS

log = logging.getLogger("safety.notify")

TITLE_MAX, BODY_MAX, FIELD_MAX = 150, 500, 200
CHAT_RE = re.compile(r"^-?\d{1,20}$")
LINE_RE = re.compile(r"^U[0-9a-f]{32}$")
UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def clean(text, limit: int) -> str:
    """ข้อความธรรมดาบรรทัดเดียว: ตัดอักขระควบคุม/ขึ้นบรรทัดใหม่ เหลือช่องว่างเดียว และจำกัดความยาว"""
    return re.sub(r"\s+", " ", UNSAFE_RE.sub(" ", "" if text is None else str(text))).strip()[:limit]


def valid_contact(channel: str, user) -> str | None:
    """ค่าติดต่อของผู้ใช้สำหรับช่องทางนั้นถ้ารูปแบบถูกต้อง ไม่เช่นนั้น None"""
    field = {"telegram": "telegram_chat_id", "line": "line_user_id", "email": "email"}.get(channel)
    try:
        value = user[field] if field else None
    except (KeyError, IndexError):
        return None
    if not isinstance(value, str):
        return None
    if channel == "telegram":
        return value if CHAT_RE.match(value) else None
    if channel == "line":
        return value if LINE_RE.match(value) else None
    if len(value) > 254 or re.search(r"[\s\x00-\x1f\x7f]", value) or value.count("@") != 1:
        return None
    local, domain = value.split("@")
    return value if local and "." in domain and not domain.startswith(".") and not domain.endswith(".") else None


def _split(text) -> list[str]:
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def recipients_for(db, rule, hazard, actor_id: int | None) -> list:
    """ผู้ใช้ที่ใช้งานอยู่ตามกฎ: reporter / owner / role:<key> (เฉพาะผู้ที่ขอบเขตครอบคลุมรายการ); ไม่รวมผู้กระทำเอง ไม่ซ้ำ"""
    found: dict[int, object] = {}

    def add(row):
        if row is not None and row["active"] and row["id"] != actor_id:
            found.setdefault(row["id"], row)

    get = lambda uid: db.execute("SELECT * FROM safety_users WHERE id=?", (uid,)).fetchone() if uid else None
    for kind in _split(rule["recipients"]):
        if kind == "reporter":
            add(get(hazard["reported_by"]))
        elif kind == "owner":
            add(get(hazard["owner_id"]))
        elif kind.startswith("role:"):
            for u in db.execute("SELECT * FROM safety_users WHERE role=? AND active=1 ORDER BY id", (kind[5:],)):
                if user_in_scope(db, u, hazard["risk_area_id"]):
                    add(u)
    return list(found.values())


def render_message(event: str, hazard, extra: Mapping | None = None) -> tuple[str, str]:
    extra = extra or {}
    code, title = clean(hazard["code"], 40), clean(hazard["title"], FIELD_MAX)
    area = clean(extra.get("area"), FIELD_MAX)
    level = f" · ระดับ {clean(hazard['level_name'], 40)} ({hazard['score']})" if hazard["level_name"] else ""
    due = f" · ครบกำหนด {clean(hazard['due_date'], 10)}" if hazard["due_date"] else ""
    where = f" · พื้นที่ {area}" if area else ""
    days = extra.get("days")
    head, tail = {
        "hazard_reported": (f"แจ้งอันตรายใหม่ {code}", f"{title}{where}"),
        "hazard_assessed": (f"ประเมินความเสี่ยง {code}", f"{title}{level}{where}"),
        "hazard_assigned": (f"มอบหมายงานแก้ไข {code}", f"{title}{due}{where}"),
        "hazard_closed": (f"ปิดรายการอันตราย {code}", f"{title} · ผลการแก้ไข: {clean(hazard['closing_note'], 200)}"),
        "hazard_rejected": (f"รายการถูกปฏิเสธ {code}", f"{title} · เหตุผล: {clean(extra.get('reason') or hazard['reject_reason'], 200)}"),
        "due_soon": (f"ใกล้ครบกำหนด {code} (อีก {days} วัน)", f"{title}{level}{due}{where}"),
        "overdue": (f"เกินกำหนด {code} ({days} วัน)", f"{title}{level}{due}{where}"),
    }.get(event, (f"แจ้งเตือน {code}", title))
    return clean(head, TITLE_MAX), clean(tail, BODY_MAX)


def _stage(event: str, hazard, offset_days: int | None) -> str:
    if event in ("due_soon", "overdue"):
        return str(offset_days)
    if event == "hazard_assigned":
        return f"{hazard['owner_id']}:{hazard['due_date']}"
    if event == "hazard_assessed":
        return str(hazard["score"])
    return "1"


def _rule_matches(rule, event: str, hazard, offset_days: int | None) -> bool:
    if event in ("due_soon", "overdue") and rule["offset_days"] != offset_days:
        return False
    if event == "hazard_assessed" and rule["min_score"] is not None:
        return hazard["score"] is not None and hazard["score"] >= rule["min_score"]
    return True


def _queue_external(db, channel: str, target: str, title: str, body: str, urgent: int, notification_id, key: str) -> None:
    db.execute("INSERT OR IGNORE INTO notify_outbox(channel, target, subject, body, urgent, notification_id, dedupe_key, next_try_at) "
               "VALUES(?,?,?,?,?,?,?, datetime('now','localtime'))", (channel, target, title, body, urgent, notification_id, key))


def _write_rows(db, event: str, rec, title: str, body: str, stage: str, ref: str, link: str, hazard_id, incident_id,
                actor_id, offset_days, urgent_base: bool, require_any: frozenset = frozenset()) -> int:
    """เขียนแถวแจ้งเตือน/คิวส่งตามกฎของเหตุการณ์ (ใช้ร่วมกันระหว่างอันตรายและอุบัติเหตุ); คืนจำนวนแถวในระบบที่สร้างใหม่"""
    created = 0
    for rule in db.execute("SELECT * FROM notify_rules WHERE event=? AND enabled=1 ORDER BY id", (event,)).fetchall():
        if not _rule_matches(rule, event, rec, offset_days):
            continue
        key = f"{event}:{ref}:{rule['id']}:{stage}"
        channels = set(_split(rule["channels"]))
        urgent = int(urgent_base or (event == "overdue" and rule["offset_days"] >= 3))
        for user in recipients_for(db, rule, rec, actor_id):
            if require_any and not (require_any & user_permissions(db, user)):
                continue                                       # ไม่แจ้งคนที่เปิดรายการนี้ไม่ได้ (ลิงก์จะ 404)
            note_id = None
            if "inapp" in channels:
                cur = db.execute("INSERT OR IGNORE INTO notifications(user_id, event, title, body, link, hazard_id, incident_id, dedupe_key) "
                                 "VALUES(?,?,?,?,?,?,?,?)", (user["id"], event, title, body, link, hazard_id, incident_id, key))
                if cur.rowcount != 1:
                    continue                                   # เคยแจ้งผู้ใช้คนนี้สำหรับขั้นนี้แล้ว — ไม่ซ้ำ ทั้งในระบบและช่องทางอื่น
                created += 1
                note_id = cur.lastrowid
            opted = set(_split(user["notify_channels"]))
            for channel in OUTBOX_CHANNELS:
                if channel == "teams" or channel not in channels or channel not in opted:
                    continue
                target = valid_contact(channel, user)
                if target:
                    _queue_external(db, channel, target, title, body, urgent, note_id, key)
        if "teams" in channels:                                # ประกาศกลุ่มหนึ่งข้อความต่อกฎ+รายการ+ขั้น
            _queue_external(db, "teams", "teams", title, body, urgent, None, key)
    return created


def emit(db, event: str, hazard_id: int, actor_id: int | None, *, offset_days: int | None = None,
         today: date | None = None, stage: str | None = None, days: int | None = None) -> int:
    """เขียนการแจ้งเตือนตามกฎของเหตุการณ์นี้ คืนจำนวนแถวแจ้งเตือนในระบบที่สร้างใหม่; ไม่ throw ไม่ commit"""
    if event not in NOTIFY_EVENTS or event.startswith("incident_"):
        return 0
    db.execute("SAVEPOINT notify")
    try:
        hazard = db.execute("SELECT * FROM hazards WHERE id=?", (hazard_id,)).fetchone()
        if hazard is None:
            db.execute("RELEASE notify")
            return 0
        area = db.execute("SELECT name_th FROM risk_areas WHERE id=?", (hazard["risk_area_id"],)).fetchone()
        title, body = render_message(event, hazard, {"area": area[0] if area else "", "days": days if days is not None else offset_days})
        stage = stage if stage is not None else _stage(event, hazard, offset_days)
        created = _write_rows(db, event, hazard, title, body, stage, str(hazard_id), f"/hazards/{hazard_id}", hazard_id, None,
                              actor_id, offset_days, bool(hazard["level_requires_approval"]))
        db.execute("RELEASE notify")
        return created
    except Exception:                                              # noqa: BLE001 — การแจ้งเตือนต้องไม่ทำให้ขั้นตอนหลักล้ม
        log.exception("emit(%s, hazard=%s) failed; rolled back", event, hazard_id)
        db.execute("ROLLBACK TO notify")
        db.execute("RELEASE notify")
        return 0


KIND_TH = {"accident": "อุบัติเหตุ", "near_miss": "Near Miss"}


def render_incident_message(event: str, incident, extra: Mapping | None = None) -> tuple[str, str]:
    """ข้อความแจ้งเตือนอุบัติเหตุ: รหัส ประเภท หัวข้อ พื้นที่ เท่านั้น — ไม่มีชื่อ/รหัสพนักงานของผู้เกี่ยวข้องเด็ดขาด"""
    extra = extra or {}
    code, title = clean(incident["code"], 40), clean(incident["title"], FIELD_MAX)
    kind = KIND_TH.get(incident["kind"], "อุบัติเหตุ")
    where = f" · พื้นที่ {clean(extra.get('area'), FIELD_MAX)}" if extra.get("area") else ""
    due = f" · ครบกำหนด {clean(incident['due_date'], 10)}" if incident["due_date"] else ""
    head, tail = {
        "incident_reported": (f"แจ้ง{kind}ใหม่ {code}", f"{title}{where}"),
        "incident_assigned": (f"มอบหมายสอบสวน{kind} {code}", f"{title}{due}{where}"),
        "incident_closed": (f"ปิดรายการ{kind} {code}", f"{title}{where}"),                  # ไม่คัดลอกข้อความอิสระ (อาจมีชื่อบุคคล)
    }.get(event, (f"แจ้งเตือน {code}", title))
    return clean(head, TITLE_MAX), clean(tail, BODY_MAX)


def emit_incident(db, event: str, incident_id: int, actor_id: int | None) -> int:
    """แจ้งเตือนเหตุการณ์อุบัติเหตุตามกฎ; สัญญาเดียวกับ emit (SAVEPOINT, ไม่ throw, ไม่ commit)"""
    if event not in NOTIFY_EVENTS or not event.startswith("incident_"):
        return 0
    db.execute("SAVEPOINT notify")
    try:
        inc = db.execute("SELECT i.*, COALESCE(s.counts_lost_time, 0) AS lost_time FROM incidents i "
                         "LEFT JOIN injury_severities s ON s.id = i.injury_severity_id WHERE i.id=?", (incident_id,)).fetchone()
        if inc is None:
            db.execute("RELEASE notify")
            return 0
        area = db.execute("SELECT name_th FROM risk_areas WHERE id=?", (inc["risk_area_id"],)).fetchone()
        title, body = render_incident_message(event, inc, {"area": area[0] if area else ""})
        stage = f"{inc['owner_id']}:{inc['due_date']}" if event == "incident_assigned" else "1"
        created = _write_rows(db, event, inc, title, body, stage, f"i{incident_id}", f"/incidents/{incident_id}", None, incident_id,
                              actor_id, None, bool(inc["lost_time"] or inc["level_requires_approval"]),
                              frozenset({"incident.report", "incident.investigate", "incident.close"}))
        db.execute("RELEASE notify")
        return created
    except Exception:                                              # noqa: BLE001
        log.exception("emit_incident(%s, incident=%s) failed; rolled back", event, incident_id)
        db.execute("ROLLBACK TO notify")
        db.execute("RELEASE notify")
        return 0
