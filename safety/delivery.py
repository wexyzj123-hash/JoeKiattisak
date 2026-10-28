"""safety/delivery.py — ส่งข้อความจากคิว notify_outbox: ช่องทาง Telegram/LINE/Teams/Email, ลองใหม่ตามตาราง, ช่วงเวลาเงียบ
หลักการ: ไม่เรียกเครือข่ายในคำขอของผู้ใช้ (ทำในตัวทำงานเบื้องหลังเท่านั้น), ความลับมาจาก environment เท่านั้นและไม่เคยถูกเขียนลง
ฐานข้อมูล/log/ข้อความผิดพลาด, ข้อความเป็นข้อความธรรมดา (ไม่มี parse_mode/HTML), อีเมลสร้างด้วย EmailMessage (กัน header injection)"""
from __future__ import annotations

import http.client
import json
import logging
import os
import re
import smtplib
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Callable, Mapping

from .db import connect
from .notify import clean

log = logging.getLogger("safety.delivery")

RETRY_MINUTES = (1, 5, 15, 60, 180)
LEASE_MINUTES = 10                       # จองแถวไว้ระหว่างส่ง — ตัวทำงานล้มกลางทางก็ถูกลองใหม่หลังหมดสัญญา
LIMITS = {"telegram": 4096, "line": 5000, "teams": 20000, "email": 5000}
SUBJECT_MAX = 150
UNCONFIGURED = "ช่องทางยังไม่ได้ตั้งค่า"
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_urlopen = urllib.request.urlopen        # แทนที่ได้ในการทดสอบ
fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")


class TransportError(Exception):
    """ข้อความต้องไม่มีความลับ/URL — แสดงในหน้าผู้ดูแลได้"""
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _timeout(env: Mapping) -> float:
    try:
        return max(0.05, float(env.get("NOTIFY_HTTP_TIMEOUT") or 10))
    except ValueError:
        return 10.0


def _text(subject: str, body: str, channel: str, sep: str = "\n") -> str:
    limit = LIMITS[channel]
    return f"{clean(subject, SUBJECT_MAX)}{sep}{clean(body, limit)}"[:limit]


def _post_json(url: str, payload: dict, headers: dict, env: Mapping, what: str) -> None:
    req = urllib.request.Request(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 {"Content-Type": "application/json; charset=utf-8", **headers}, method="POST")
    try:
        with _urlopen(req, timeout=_timeout(env)) as resp:
            status = getattr(resp, "status", 200)
            resp.read()
    except urllib.error.HTTPError as exc:
        raise TransportError(f"{what} HTTP {exc.code}", retryable=exc.code == 429 or exc.code >= 500) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, http.client.HTTPException):
        raise TransportError(f"{what}: เชื่อมต่อไม่สำเร็จหรือหมดเวลา", retryable=True) from None
    if not 200 <= status < 300:
        raise TransportError(f"{what} HTTP {status}", retryable=status >= 500)


def send_telegram(target: str, subject: str, body: str, env: Mapping) -> None:
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TransportError(UNCONFIGURED, retryable=False)
    base = (env.get("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")
    _post_json(f"{base}/bot{token}/sendMessage", {"chat_id": target, "text": _text(subject, body, "telegram"),
                                                   "disable_web_page_preview": True}, {}, env, "Telegram")


def send_line(target: str, subject: str, body: str, env: Mapping) -> None:
    token = env.get("LINE_CHANNEL_TOKEN")
    if not token:
        raise TransportError(UNCONFIGURED, retryable=False)
    base = (env.get("LINE_API_BASE") or "https://api.line.me").rstrip("/")
    _post_json(f"{base}/v2/bot/message/push", {"to": target, "messages": [{"type": "text", "text": _text(subject, body, "line")}]},
               {"Authorization": f"Bearer {token}"}, env, "LINE")


def send_teams(target: str, subject: str, body: str, env: Mapping) -> None:
    url = (env.get("TEAMS_WEBHOOK_URL") or "").strip()
    if not url.lower().startswith("https://"):
        raise TransportError(UNCONFIGURED, retryable=False)
    _post_json(url, {"text": _text(subject, body, "teams", "\n\n")}, {}, env, "Teams")


def send_email(target: str, subject: str, body: str, env: Mapping) -> None:
    host = env.get("SMTP_HOST")
    if not host:
        raise TransportError(UNCONFIGURED, retryable=False)
    msg = EmailMessage()
    try:
        msg["From"] = env.get("SMTP_FROM") or env.get("SMTP_USER") or "aiat-safety@localhost"
        msg["To"] = target
        msg["Subject"] = clean(subject, SUBJECT_MAX)
        msg.set_content(clean(body, LIMITS["email"]), charset="utf-8")
    except (ValueError, TypeError):
        raise TransportError("ที่อยู่อีเมลไม่ถูกต้อง", retryable=False) from None
    try:
        port = int(env.get("SMTP_PORT") or 25)
        with smtplib.SMTP(host, port, timeout=_timeout(env), local_hostname="aiat-safety.local") as smtp:
            if str(env.get("SMTP_TLS", "")).lower() in ("1", "true", "yes", "starttls"):
                smtp.starttls()
            if env.get("SMTP_USER"):
                smtp.login(env["SMTP_USER"], env.get("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise TransportError("SMTP: ยืนยันตัวตนไม่สำเร็จ", retryable=False) from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
        raise TransportError("SMTP: เซิร์ฟเวอร์ปฏิเสธผู้ส่ง/ผู้รับ", retryable=False) from None
    except smtplib.SMTPResponseException as exc:
        raise TransportError(f"SMTP ตอบกลับ {exc.smtp_code}", retryable=exc.smtp_code < 500) from None
    except (smtplib.SMTPException, OSError, socket.timeout):
        raise TransportError("SMTP: เชื่อมต่อไม่สำเร็จหรือหมดเวลา", retryable=True) from None


REAL = {"telegram": send_telegram, "line": send_line, "teams": send_teams, "email": send_email}


# ---------- ช่วงเวลาเงียบ ----------
def _minutes(hhmm) -> int | None:
    m = HHMM_RE.match(hhmm) if isinstance(hhmm, str) else None
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def in_quiet_hours(now: datetime, start, end) -> bool:
    """เริ่มรวมขอบ สิ้นสุดไม่รวมขอบ; ข้ามเที่ยงคืนได้; ค่าผิดรูปแบบหรือเริ่ม=สิ้นสุด = ไม่มีช่วงเงียบ"""
    s, e = _minutes(start), _minutes(end)
    if s is None or e is None or s == e:
        return False
    cur = now.hour * 60 + now.minute
    return s <= cur < e if s < e else cur >= s or cur < e


def quiet_end_after(now: datetime, start, end) -> datetime:
    if not in_quiet_hours(now, start, end):
        return now
    e = _minutes(end)
    end_today = now.replace(hour=e // 60, minute=e % 60, second=0, microsecond=0)
    return end_today if now < end_today else end_today + timedelta(days=1)


def _setting(db, key: str, default: str) -> str:
    row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def defer_if_quiet(db, now: datetime) -> int:
    """ข้อความที่ไม่เร่งด่วนและกำหนดส่งก่อนสิ้นสุดช่วงเงียบ → เลื่อนไปสิ้นสุดช่วงเงียบ; คืนจำนวนที่เลื่อน"""
    start, end = _setting(db, "quiet_start", "22:00"), _setting(db, "quiet_end", "06:00")
    if not in_quiet_hours(now, start, end):
        return 0
    until = fmt(quiet_end_after(now, start, end))
    return db.execute("UPDATE notify_outbox SET next_try_at=? WHERE status IN ('pending','failed') AND urgent=0 AND next_try_at < ?",
                      (until, until)).rowcount


# ---------- การส่ง ----------
def _claim(db, outbox_id: int, now: datetime) -> bool:
    """จองแถวแบบ atomic (เพิ่มจำนวนครั้งและเลื่อนกำหนดเป็นสัญญายืม) — ตัวทำงานสองตัวจะไม่ส่งแถวเดียวกันซ้ำ"""
    cur = db.execute("UPDATE notify_outbox SET attempts=attempts+1, next_try_at=? WHERE id=? "
                     "AND status IN ('pending','failed') AND next_try_at <= ?",
                     (fmt(now + timedelta(minutes=LEASE_MINUTES)), outbox_id, fmt(now)))
    db.commit()
    return cur.rowcount == 1


def deliver_due(db, now: datetime, transports: Mapping[str, Callable] | None = None, env: Mapping | None = None,
                limit: int = 50) -> dict:
    env = os.environ if env is None else env
    transports = REAL if transports is None else transports
    result = {"sent": 0, "retry": 0, "dead": 0, "deferred": 0}
    result["deferred"] = defer_if_quiet(db, now)
    db.commit()
    due = db.execute("SELECT id FROM notify_outbox WHERE status IN ('pending','failed') AND next_try_at <= ? "
                     "ORDER BY urgent DESC, id LIMIT ?", (fmt(now), limit)).fetchall()
    for (outbox_id,) in due:
        if not _claim(db, outbox_id, now):
            continue
        row = db.execute("SELECT * FROM notify_outbox WHERE id=?", (outbox_id,)).fetchone()
        try:
            send = transports.get(row["channel"])
            if send is None:
                raise TransportError(UNCONFIGURED, retryable=False)
            send(row["target"], row["subject"], row["body"], env)
        except TransportError as exc:
            retryable, message = exc.retryable, str(exc)
        except Exception as exc:                                       # noqa: BLE001 — ไม่เขียนข้อความจริงลง log (อาจมีความลับ)
            log.warning("outbox %s: unexpected %s", outbox_id, type(exc).__name__)
            retryable, message = True, "ส่งไม่สำเร็จ (ข้อผิดพลาดภายใน) จะลองใหม่"
        else:
            db.execute("UPDATE notify_outbox SET status='sent', sent_at=?, last_error='' WHERE id=?", (fmt(now), outbox_id))
            db.commit()
            result["sent"] += 1
            continue
        attempts = row["attempts"]
        if retryable and attempts <= len(RETRY_MINUTES):
            db.execute("UPDATE notify_outbox SET status='failed', last_error=?, next_try_at=? WHERE id=?",
                       (message[:300], fmt(now + timedelta(minutes=RETRY_MINUTES[attempts - 1])), outbox_id))
            result["retry"] += 1
        else:
            db.execute("UPDATE notify_outbox SET status='dead', last_error=? WHERE id=?", (message[:300], outbox_id))
            result["dead"] += 1
        db.commit()
    return result


def start_worker(app, interval: float = 30.0) -> threading.Thread:
    """ตัวทำงานเบื้องหลัง (daemon): ส่งข้อความที่ถึงกำหนดทุก interval วินาที; ข้อผิดพลาดถูก log แล้วทำงานต่อ"""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                con = connect(app.config["DATABASE"])
                try:
                    deliver_due(con, datetime.now())
                finally:
                    con.close()
            except Exception:                                          # noqa: BLE001
                log.exception("delivery worker iteration failed")
            stop.wait(interval)

    thread = threading.Thread(target=loop, name="safety-delivery", daemon=True)
    thread.stop_event = stop
    thread.start()
    return thread
