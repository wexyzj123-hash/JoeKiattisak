import json
import logging
import socketserver
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from safety import delivery
from safety.db import connect
from safety.delivery import (RETRY_MINUTES, TransportError, deliver_due, defer_if_quiet, in_quiet_hours, quiet_end_after,
                             send_email, send_line, send_teams, send_telegram, start_worker)
from safety.schema import ensure_schema

NOW = datetime(2026, 3, 10, 12, 0, 0)
F = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")
SECRET = "SUPER-SECRET-TOKEN-123"


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def add(db, channel="telegram", target="1", subject="หัวข้อ", body="เนื้อหา", urgent=0, status="pending", due=None, key=None, attempts=0):
    cur = db.execute("INSERT INTO notify_outbox(channel,target,subject,body,urgent,status,next_try_at,dedupe_key,attempts) VALUES(?,?,?,?,?,?,?,?,?)",
                     (channel, target, subject, body, urgent, status, F(due or NOW - timedelta(minutes=1)), key or f"k{time.monotonic_ns()}", attempts))
    db.commit()
    return cur.lastrowid


def row(db, rid):
    return db.execute("SELECT * FROM notify_outbox WHERE id=?", (rid,)).fetchone()


class Fake:
    """transport ปลอมที่บันทึกการเรียกและทำตามที่สั่ง"""
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, target, subject, body, env):
        self.calls.append((target, subject, body))
        if self.error:
            raise self.error


def fakes(**kw):
    return {c: kw.get(c, Fake()) for c in ("telegram", "line", "teams", "email")}


# ---------- ตรรกะส่ง/ลองใหม่ ----------
def test_success_marks_sent(db):
    rid = add(db)
    t = fakes()
    assert deliver_due(db, NOW, t, {})["sent"] == 1
    r = row(db, rid)
    assert (r["status"], r["attempts"], r["sent_at"]) == ("sent", 1, F(NOW)) and t["telegram"].calls == [("1", "หัวข้อ", "เนื้อหา")]
    assert deliver_due(db, NOW + timedelta(hours=1), t, {})["sent"] == 0 and len(t["telegram"].calls) == 1       # ไม่ส่งซ้ำ


def test_not_yet_due_and_cancelled_rows_are_untouched(db):
    later, gone = add(db, due=NOW + timedelta(minutes=5)), add(db, status="cancelled")
    t = fakes()
    deliver_due(db, NOW, t, {})
    assert t["telegram"].calls == [] and row(db, later)["status"] == "pending" and row(db, gone)["status"] == "cancelled"


def test_retryable_failure_backoff_schedule_then_dead(db):
    rid = add(db)
    t = fakes(telegram=Fake(TransportError("ล้มเหลวชั่วคราว", retryable=True)))
    now = NOW
    for attempt, minutes in enumerate(RETRY_MINUTES, 1):
        deliver_due(db, now, t, {})
        r = row(db, rid)
        assert (r["status"], r["attempts"], r["next_try_at"]) == ("failed", attempt, F(now + timedelta(minutes=minutes)))
        now = now + timedelta(minutes=minutes)
    assert RETRY_MINUTES == (1, 5, 15, 60, 180)
    deliver_due(db, now, t, {})
    r = row(db, rid)
    assert (r["status"], r["attempts"]) == ("dead", 6) and "ล้มเหลวชั่วคราว" in r["last_error"]


def test_non_retryable_goes_dead_immediately(db):
    rid = add(db)
    deliver_due(db, NOW, fakes(telegram=Fake(TransportError("ผู้ใช้บล็อกบอท", retryable=False))), {})
    assert (row(db, rid)["status"], row(db, rid)["attempts"]) == ("dead", 1)


def test_unexpected_exception_is_retryable_and_message_is_generic(db):
    rid = add(db)
    deliver_due(db, NOW, fakes(telegram=Fake(RuntimeError(f"boom with {SECRET}"))), {})
    r = row(db, rid)
    assert r["status"] == "failed" and SECRET not in r["last_error"]


def test_unconfigured_channel_dead_with_reason_and_no_secret_in_error(db):
    rids = [add(db, c) for c in ("telegram", "line", "teams", "email")]
    deliver_due(db, NOW, None, {"SMTP_PASSWORD": SECRET})                     # transport จริง ไม่มีโทเค็น/โฮสต์
    for rid in rids:
        r = row(db, rid)
        assert r["status"] == "dead" and r["last_error"] == "ช่องทางยังไม่ได้ตั้งค่า"


def test_two_workers_never_double_send(db, tmp_path):
    rid = add(db)
    other = connect(str(tmp_path / "s.db"))
    assert delivery._claim(other, rid, NOW) is True                              # worker B จองแล้ว (ยังส่งไม่เสร็จ)
    t = fakes()
    assert deliver_due(db, NOW, t, {})["sent"] == 0 and t["telegram"].calls == []
    assert delivery._claim(db, rid, NOW) is False
    other.close()


def test_claim_lease_expires_after_crash(db):
    rid = add(db)
    assert delivery._claim(db, rid, NOW) is True                                 # worker ล้มหลังจอง
    t = fakes()
    assert deliver_due(db, NOW + timedelta(minutes=30), t, {})["sent"] == 1


def test_urgent_rows_go_first_and_limit_applies(db):
    low = [add(db, target=str(i)) for i in range(3)]
    hot = add(db, target="urgent", urgent=1)
    t = fakes()
    deliver_due(db, NOW, t, {}, limit=2)
    assert t["telegram"].calls[0][0] == "urgent" and len(t["telegram"].calls) == 2


# ---------- ช่วงเวลาเงียบ ----------
@pytest.mark.parametrize("hhmm, expected", [("21:59", False), ("22:00", True), ("23:59", True), ("00:00", True), ("05:59", True), ("06:00", False), ("12:00", False)])
def test_quiet_hours_window_crossing_midnight(hhmm, expected):
    h, m = map(int, hhmm.split(":"))
    assert in_quiet_hours(datetime(2026, 3, 10, h, m), "22:00", "06:00") is expected


def test_quiet_hours_same_day_window_and_start_equals_end_means_never():
    assert in_quiet_hours(datetime(2026, 3, 10, 13, 0), "12:00", "14:00") and not in_quiet_hours(datetime(2026, 3, 10, 14, 0), "12:00", "14:00")
    assert in_quiet_hours(datetime(2026, 3, 10, 12, 0), "12:00", "14:01") is True                # เริ่มต้นรวมขอบเริ่ม
    assert not any(in_quiet_hours(datetime(2026, 3, 10, h, 0), "08:00", "08:00") for h in range(24))


@pytest.mark.parametrize("bad", ["", "25:00", "7:5", "ab", "12:60", None])
def test_bad_quiet_setting_means_no_quiet_hours(bad):
    assert in_quiet_hours(datetime(2026, 3, 10, 23, 0), bad, "06:00") is False


def test_quiet_end_after_rolls_to_next_morning():
    assert quiet_end_after(datetime(2026, 3, 10, 23, 30), "22:00", "06:00") == datetime(2026, 3, 11, 6, 0)
    assert quiet_end_after(datetime(2026, 3, 10, 3, 0), "22:00", "06:00") == datetime(2026, 3, 10, 6, 0)
    assert quiet_end_after(datetime(2026, 3, 10, 12, 0), "22:00", "06:00") == datetime(2026, 3, 10, 12, 0)     # ไม่อยู่ในช่วงเงียบ


def test_non_urgent_waits_for_quiet_end_but_urgent_and_other_rows_are_unaffected(db):
    night = datetime(2026, 3, 10, 23, 0)
    calm, hot = add(db, target="calm", due=night - timedelta(minutes=1)), add(db, target="hot", urgent=1, due=night - timedelta(minutes=1))
    t = fakes()
    res = deliver_due(db, night, t, {})
    assert [c[0] for c in t["telegram"].calls] == ["hot"] and res["sent"] == 1 and res["deferred"] == 1
    assert row(db, calm)["next_try_at"] == F(datetime(2026, 3, 11, 6, 0)) and row(db, calm)["status"] == "pending"
    deliver_due(db, datetime(2026, 3, 11, 5, 59), t, {})
    assert len(t["telegram"].calls) == 1
    deliver_due(db, datetime(2026, 3, 11, 6, 0), t, {})
    assert [c[0] for c in t["telegram"].calls] == ["hot", "calm"]


def test_retry_scheduled_inside_quiet_hours_also_waits(db):
    night = datetime(2026, 3, 10, 22, 30)
    rid = add(db, status="failed", attempts=1, due=night - timedelta(minutes=1))
    t = fakes()
    deliver_due(db, night, t, {})
    assert t["telegram"].calls == [] and row(db, rid)["next_try_at"] == F(datetime(2026, 3, 11, 6, 0))


def test_defer_if_quiet_returns_count_and_respects_setting_change(db):
    night = datetime(2026, 3, 10, 23, 0)
    add(db, due=night - timedelta(minutes=1)); add(db, urgent=1, due=night - timedelta(minutes=1))
    assert defer_if_quiet(db, night) == 1
    db.execute("UPDATE app_settings SET value='08:00' WHERE key='quiet_start'"); db.execute("UPDATE app_settings SET value='08:00' WHERE key='quiet_end'"); db.commit()
    add(db, due=night - timedelta(minutes=1))
    assert defer_if_quiet(db, night) == 0                                       # start == end → ไม่มีช่วงเงียบ


# ---------- การส่งจริงผ่านเซิร์ฟเวอร์จำลอง ----------
class Stub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, status=200, delay=0.0):
        self.status, self.delay, self.requests = status, delay, []
        super().__init__(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def _handler(self):
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body or b"{}")})
                time.sleep(stub.delay)
                self.send_response(stub.status); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        return H

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


@pytest.fixture
def stub():
    servers = []

    def make(**kw):
        s = Stub(**kw); servers.append(s); return s
    yield make
    for s in servers:
        s.shutdown(); s.server_close()


TG_ENV = lambda s, **kw: {"TELEGRAM_BOT_TOKEN": SECRET, "TELEGRAM_API_BASE": s.base, "NOTIFY_HTTP_TIMEOUT": "0.4", **kw}


@pytest.mark.parametrize("status, outcome", [(200, "sent"), (403, "dead"), (400, "dead"), (401, "dead"), (429, "failed"), (500, "failed"), (503, "failed")])
def test_telegram_http_transport_against_local_server(db, stub, status, outcome):
    s = stub(status=status)
    rid = add(db, target="-100123", subject="ก", body="ข")
    deliver_due(db, NOW, None, TG_ENV(s))
    r = row(db, rid)
    assert r["status"] == outcome and SECRET not in r["last_error"] and "127.0.0.1" not in r["last_error"]
    req = s.requests[0]
    assert req["path"] == f"/bot{SECRET}/sendMessage" and req["body"]["chat_id"] == "-100123"
    assert "parse_mode" not in req["body"] and req["body"]["text"] == "ก\nข"


def test_telegram_timeout_and_connection_refused_are_retryable(db, stub):
    slow = stub(delay=1.0)
    rid = add(db)
    deliver_due(db, NOW, None, TG_ENV(slow))
    assert row(db, rid)["status"] == "failed"
    rid2 = add(db, key="other")
    deliver_due(db, NOW, None, {"TELEGRAM_BOT_TOKEN": SECRET, "TELEGRAM_API_BASE": "http://127.0.0.1:9", "NOTIFY_HTTP_TIMEOUT": "0.4"})
    r = row(db, rid2)
    assert r["status"] == "failed" and SECRET not in r["last_error"]


def test_line_http_transport_against_local_server(db, stub):
    s = stub()
    env = {"LINE_CHANNEL_TOKEN": SECRET, "LINE_API_BASE": s.base, "NOTIFY_HTTP_TIMEOUT": "0.4"}
    rid = add(db, "line", "U" + "a" * 32, "หัวข้อ", "เนื้อหา")
    deliver_due(db, NOW, None, env)
    assert row(db, rid)["status"] == "sent"
    req = s.requests[0]
    assert req["path"] == "/v2/bot/message/push" and req["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert req["body"] == {"to": "U" + "a" * 32, "messages": [{"type": "text", "text": "หัวข้อ\nเนื้อหา"}]}
    s.status = 403
    rid2 = add(db, "line", "U" + "b" * 32)
    deliver_due(db, NOW, None, env)
    assert row(db, rid2)["status"] == "dead"


def test_teams_requires_https_url_from_env(db, monkeypatch):
    for url in ("", "http://example.com/hook", "ftp://x/y", "javascript:alert(1)"):
        with pytest.raises(TransportError) as e:
            send_teams("teams", "s", "b", {"TEAMS_WEBHOOK_URL": url})
        assert e.value.retryable is False and "ยังไม่ได้ตั้งค่า" in str(e.value)
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"1"

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, body=json.loads(req.data), timeout=timeout)
        return Resp()
    monkeypatch.setattr(delivery, "_urlopen", fake_urlopen)
    send_teams("teams", "หัวข้อ", "เนื้อหา", {"TEAMS_WEBHOOK_URL": "https://example.webhook.office.com/abc"})
    assert seen["url"] == "https://example.webhook.office.com/abc" and seen["body"] == {"text": "หัวข้อ\n\nเนื้อหา"}


class SMTPStub(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, auth_fail=False, advertise_auth=False):
        self.messages, self.auth_fail, self.advertise_auth = [], auth_fail, advertise_auth
        super().__init__(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def _handler(self):
        srv = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda s: self.wfile.write((s + "\r\n").encode())
                w("220 stub ready")
                mail = {"to": []}
                while True:
                    line = self.rfile.readline().decode("utf-8", "replace").strip()
                    if not line:
                        return
                    cmd = line.split(" ")[0].upper()
                    if cmd in ("EHLO", "HELO"):
                        w("250-stub")
                        w("250 AUTH PLAIN LOGIN" if srv.advertise_auth else "250 OK")
                    elif cmd == "AUTH":
                        w("535 5.7.8 authentication failed")
                    elif cmd == "MAIL":
                        mail["from"] = line
                        w("250 OK")
                    elif cmd == "RCPT":
                        mail["to"].append(line)
                        w("250 OK")
                    elif cmd == "DATA":
                        w("354 go")
                        data = []
                        while True:
                            l = self.rfile.readline().decode("utf-8", "replace")
                            if l in (".\r\n", ".\n", ""):
                                break
                            data.append(l)
                        mail["data"] = "".join(data)
                        srv.messages.append(mail)
                        w("250 queued")
                    elif cmd == "QUIT":
                        w("221 bye")
                        return
                    else:
                        w("250 OK")
        return H


@pytest.fixture
def smtp():
    servers = []

    def make(**kw):
        s = SMTPStub(**kw); servers.append(s); return s
    yield make
    for s in servers:
        s.shutdown(); s.server_close()


def smtp_env(s, **kw):
    return {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(s.server_address[1]), "SMTP_FROM": "safety@example.com", "NOTIFY_HTTP_TIMEOUT": "2", **kw}


def test_smtp_transport_delivers_plain_text_utf8(db, smtp):
    s = smtp()
    rid = add(db, "email", "boss@example.com", "แจ้งเตือน HZ-1", "ข้อความภาษาไทย")
    deliver_due(db, NOW, None, smtp_env(s))
    assert row(db, rid)["status"] == "sent" and len(s.messages) == 1
    data = s.messages[0]["data"]
    assert "boss@example.com" in s.messages[0]["to"][0] and "safety@example.com" in s.messages[0]["from"]
    assert "Subject:" in data and "text/plain" in data and "charset=\"utf-8\"" in data


def test_smtp_auth_failure_goes_dead_without_password_leak(db, smtp):
    s = smtp(advertise_auth=True)
    rid = add(db, "email", "a@b.co")
    deliver_due(db, NOW, None, smtp_env(s, SMTP_USER="u", SMTP_PASSWORD=SECRET))
    r = row(db, rid)
    assert r["status"] == "dead" and SECRET not in r["last_error"]


def test_smtp_connection_refused_is_retryable(db):
    rid = add(db, "email", "a@b.co")
    deliver_due(db, NOW, None, {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": "9", "SMTP_FROM": "x@y.zz", "SMTP_PASSWORD": SECRET})
    r = row(db, rid)
    assert r["status"] == "failed" and SECRET not in r["last_error"]


# ---------- การฉีดเนื้อหา ----------
EVIL_SUBJECT = "หัวข้อ\r\nBcc: evil@example.com\r\nX-Injected: 1"
EVIL_BODY = "@everyone <b>hi</b> *md* [x](http://evil) \x1b[31m" + "ก" * 10000


def test_email_header_injection_blocked_and_single_subject(db, smtp):
    s = smtp()
    rid = add(db, "email", "a@b.co", EVIL_SUBJECT, EVIL_BODY)
    deliver_due(db, NOW, None, smtp_env(s))
    assert row(db, rid)["status"] == "sent"
    import email
    parsed = email.message_from_string(s.messages[0]["data"])
    assert parsed.get_all("Subject") and len(parsed.get_all("Subject")) == 1              # หัวข้อเดียว
    assert parsed["Bcc"] is None and parsed["X-Injected"] is None                          # ไม่มี header ที่ถูกฉีดเข้ามา
    assert sorted(k.lower() for k in parsed.keys()) == ["content-transfer-encoding", "content-type", "from", "mime-version", "subject", "to"]
    assert "evil@example.com" not in s.messages[0]["to"][0]


def test_telegram_and_line_plain_text_and_length_limits(db, stub):
    s = stub()
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_API_BASE": s.base, "LINE_CHANNEL_TOKEN": "l", "LINE_API_BASE": s.base, "NOTIFY_HTTP_TIMEOUT": "2"}
    add(db, "telegram", "1", EVIL_SUBJECT, EVIL_BODY); add(db, "line", "U" + "a" * 32, EVIL_SUBJECT, EVIL_BODY)
    deliver_due(db, NOW, None, env)
    tg = next(r for r in s.requests if "sendMessage" in r["path"])["body"]
    ln = next(r for r in s.requests if "push" in r["path"])["body"]
    assert "parse_mode" not in tg and len(tg["text"]) <= 4096 and len(ln["messages"][0]["text"]) <= 5000
    for text in (tg["text"], ln["messages"][0]["text"]):
        assert "\r" not in text and "\x1b" not in text and "@everyone" in text                  # ส่งเป็นข้อความธรรมดา (ไม่แปลความ)
        assert "Bcc:" in text                                                                    # ไม่ใช่ header แต่ข้อความธรรมดาบรรทัดเดียว
        assert text.count("\n") == 1                                                             # คั่นหัวข้อ/เนื้อหาด้วยบรรทัดเดียวเท่านั้น


def test_teams_text_truncated(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    monkeypatch.setattr(delivery, "_urlopen", lambda req, timeout: (seen.update(b=json.loads(req.data)) or Resp()))
    send_teams("teams", EVIL_SUBJECT, EVIL_BODY, {"TEAMS_WEBHOOK_URL": "https://h.example/x"})
    assert len(seen["b"]["text"]) <= 20000 and "\x1b" not in seen["b"]["text"]


# ---------- ความลับไม่รั่ว ----------
def test_secrets_never_in_logs_or_errors(db, stub, caplog):
    s = stub(status=500)
    add(db, "telegram", "1"); add(db, "line", "U" + "a" * 32); add(db, "email", "a@b.co")
    env = TG_ENV(s, LINE_CHANNEL_TOKEN=SECRET + "L", LINE_API_BASE=s.base, SMTP_HOST="127.0.0.1", SMTP_PORT="9",
                 SMTP_PASSWORD=SECRET + "P", SMTP_USER="u", SMTP_FROM="x@y.zz")
    with caplog.at_level(logging.DEBUG):
        deliver_due(db, NOW, None, env)
    blob = caplog.text + " ".join(r["last_error"] for r in db.execute("SELECT last_error FROM notify_outbox"))
    assert SECRET not in blob
    assert "ล้มเหลว" in blob or "HTTP 500" in blob


# ---------- ตัวทำงานเบื้องหลัง ----------
def test_worker_survives_exceptions_and_keeps_running(tmp_path, monkeypatch):
    from safety.app import create_app
    from tests.conftest import SECRET as SSO
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "w.db"), "SSO_SECRET": SSO, "SERVICE_TOKEN": "x", "HUB_URL": "http://h"})
    calls = {"n": 0}

    def flaky(db, now, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return {}
    monkeypatch.setattr(delivery, "deliver_due", flaky)
    th = start_worker(app, interval=0.02)
    deadline = time.time() + 3
    while calls["n"] < 3 and time.time() < deadline:
        time.sleep(0.02)
    th.stop_event.set(); th.join(timeout=2)
    assert calls["n"] >= 3 and th.daemon is True and not th.is_alive()


# ---------- TLS ของอีเมลต้องตรวจใบรับรองเซิร์ฟเวอร์ ----------
class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, local_hostname=None, context=None):
        self.kind, self.context, self.starttls_context, self.logged_in, self.sent = type(self).__name__, context, None, None, 0
        FakeSMTP.instances.append(self)

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self, context=None): self.starttls_context = context
    def login(self, user, password): self.logged_in = (user, password)
    def send_message(self, msg): self.sent += 1


class FakeSMTPS(FakeSMTP):
    pass


def _verifying(ctx):
    import ssl
    return ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


@pytest.mark.parametrize("mode", ["starttls", "1", "true", "yes", "STARTTLS"])
def test_starttls_verifies_server_certificate_before_login(monkeypatch, mode):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(delivery.smtplib, "SMTP", FakeSMTP)
    send_email("a@b.co", "s", "b", {"SMTP_HOST": "mail.example", "SMTP_TLS": mode, "SMTP_USER": "u", "SMTP_PASSWORD": "pw"})
    inst = FakeSMTP.instances[0]
    assert _verifying(inst.starttls_context), "starttls() ต้องส่ง ssl context ที่ตรวจใบรับรอง+ชื่อโฮสต์"
    assert inst.logged_in == ("u", "pw") and inst.sent == 1


@pytest.mark.parametrize("mode", ["ssl", "smtps", "SSL"])
def test_smtps_uses_verifying_context(monkeypatch, mode):
    FakeSMTPS.instances.clear()
    monkeypatch.setattr(delivery.smtplib, "SMTP_SSL", FakeSMTPS)
    monkeypatch.setattr(delivery.smtplib, "SMTP", lambda *a, **k: pytest.fail("ต้องใช้ SMTP_SSL"))
    send_email("a@b.co", "s", "b", {"SMTP_HOST": "mail.example", "SMTP_PORT": "465", "SMTP_TLS": mode})
    assert _verifying(FakeSMTPS.instances[0].context) and FakeSMTPS.instances[0].sent == 1


def test_plain_smtp_without_tls_setting_does_not_call_starttls(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(delivery.smtplib, "SMTP", FakeSMTP)
    send_email("a@b.co", "s", "b", {"SMTP_HOST": "mail.example"})
    assert FakeSMTP.instances[0].starttls_context is None
