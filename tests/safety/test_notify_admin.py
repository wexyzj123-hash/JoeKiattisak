import re

import pytest

from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard
from tests.safety.helpers import login, make_user, sql

URL = "/admin/notifications"


def post(c, **data):
    return c.post(URL, data={"_csrf": csrf(c), **data})


def rule_form(**kw):
    return {"action": "rule_create", "event": "hazard_closed", "recipients": "reporter,owner", "channels": ["inapp", "telegram"],
            "offset_days": "0", "min_score": "", **kw}


def rules(app, where="1=1"):
    return sql(app, f"SELECT * FROM notify_rules WHERE {where} ORDER BY id")


def test_page_requires_users_manage(safety_app):
    assert safety_app.test_client().get(URL).status_code == 302
    for role in ("viewer", "assessor", "reporter"):
        c = login(safety_app, role)
        assert c.get(URL).status_code == 403 and post(c, **rule_form()).status_code == 403
    html = login(safety_app, "admin").get(URL).get_data(as_text=True)
    assert "role:assessor" in html and "ช่วงเวลาเงียบ" in html


def test_create_update_toggle_delete_rule(safety_app):
    c = login(safety_app)
    n0 = len(rules(safety_app))
    assert post(c, **rule_form(recipients="reporter, owner ,reporter")).status_code == 302
    r = rules(safety_app)[-1]
    assert (r["event"], r["recipients"], r["channels"], r["offset_days"], r["min_score"], r["enabled"]) == \
        ("hazard_closed", "reporter,owner", "inapp,telegram", 0, None, 1) and len(rules(safety_app)) == n0 + 1
    post(c, action="rule_update", id=str(r["id"]), event="hazard_closed", recipients="owner", channels=["inapp", "line", "email"], offset_days="0", min_score="")
    r = rules(safety_app, f"id={r['id']}")[0]
    assert (r["recipients"], r["channels"]) == ("owner", "inapp,line,email")
    post(c, action="rule_toggle", id=str(r["id"]))
    assert rules(safety_app, f"id={r['id']}")[0]["enabled"] == 0
    post(c, action="rule_delete", id=str(r["id"]))
    assert rules(safety_app, f"id={r['id']}") == [] and len(rules(safety_app)) == n0


@pytest.mark.parametrize("bad", [
    {"event": "bogus"}, {"event": ""}, {"recipients": "nobody"}, {"recipients": "role:ghost"}, {"recipients": "role:"}, {"recipients": ""},
    {"recipients": "reporter,,<script>"}, {"channels": ["sms"]}, {"channels": []}, {"offset_days": "-1"}, {"offset_days": "61"},
    {"offset_days": "999999999999"}, {"offset_days": "abc"}, {"min_score": "0"}, {"min_score": "-5"}, {"min_score": "abc"},
    {"min_score": "5"},                                                                  # min_score ใช้กับ hazard_assessed เท่านั้น
    {"event": "hazard_closed", "offset_days": "2"},                                      # เหตุการณ์ทั่วไปต้อง offset 0
    {"event": "overdue", "offset_days": "0"},                                            # overdue ต้อง ≥ 1
])
def test_rule_validation_rejects(safety_app, bad):
    c = login(safety_app)
    n0 = len(rules(safety_app))
    r = post(c, **rule_form(**bad))
    assert r.status_code in (200, 302) and len(rules(safety_app)) == n0


def test_valid_edge_values_accepted(safety_app):
    c = login(safety_app)
    n0 = len(rules(safety_app))
    post(c, **rule_form(event="overdue", offset_days="60", recipients="role:admin"))
    post(c, **rule_form(event="due_soon", offset_days="0", recipients="owner"))
    post(c, **rule_form(event="hazard_assessed", recipients="role:dept_head", min_score="1", channels=["inapp"]))
    post(c, **rule_form(event="hazard_reported", recipients="", channels=["teams"]))             # ประกาศกลุ่มไม่ต้องมีผู้รับรายบุคคล
    assert len(rules(safety_app)) == n0 + 4


def test_custom_role_recipient_accepted(safety_app):
    c = login(safety_app)
    c.post("/admin/roles", data={"_csrf": csrf(c), "action": "create", "key": "shift_lead", "name_th": "หัวหน้ากะ", "permissions": ["master.view"]})
    n0 = len(rules(safety_app))
    post(c, **rule_form(recipients="role:shift_lead"))
    assert len(rules(safety_app)) == n0 + 1


def test_duplicate_rule_rejected_order_insensitive(safety_app):
    c = login(safety_app)
    post(c, **rule_form(recipients="reporter,owner", channels=["inapp", "telegram"]))
    n = len(rules(safety_app))
    post(c, **rule_form(recipients="owner,reporter", channels=["telegram", "inapp"]))
    assert len(rules(safety_app)) == n
    post(c, **rule_form(recipients="owner,reporter", channels=["inapp"]))                      # ช่องทางต่าง = ไม่ซ้ำ
    assert len(rules(safety_app)) == n + 1
    default = rules(safety_app, "event='due_soon'")[0]
    post(c, action="rule_update", id=str(default["id"]), event="hazard_closed", recipients="reporter,owner", channels=["inapp", "telegram"], offset_days="0", min_score="")
    assert rules(safety_app, f"id={default['id']}")[0]["event"] == "due_soon"                 # ชนกับกฎที่มีอยู่ → ไม่แก้


def test_update_missing_rule_is_harmless(safety_app):
    c = login(safety_app)
    assert post(c, action="rule_update", id="9999", **{k: v for k, v in rule_form().items() if k != "action"}).status_code == 302
    assert post(c, action="rule_delete", id="abc").status_code == 302


def test_disabling_every_rule_allowed_but_warns(safety_app):
    c = login(safety_app)
    for r in rules(safety_app):
        post(c, action="rule_toggle", id=str(r["id"]))
    assert not rules(safety_app, "enabled=1")
    assert "ปิดกฎทั้งหมด" in c.get(URL).get_data(as_text=True)


def test_settings_validation_hhmm(safety_app):
    c = login(safety_app)
    get = lambda k: sql(safety_app, "SELECT value FROM app_settings WHERE key=?", (k,))[0][0]
    post(c, action="settings_save", quiet_start="23:15", quiet_end="05:45", daily_time="08:05")
    assert (get("quiet_start"), get("quiet_end"), get("daily_time")) == ("23:15", "05:45", "08:05")
    for bad in ("24:00", "7:5", "12:60", "", "ab:cd", "07:30:00", "07-30"):
        post(c, action="settings_save", quiet_start=bad, quiet_end="05:45", daily_time="08:05")
        post(c, action="settings_save", quiet_start="23:15", quiet_end="05:45", daily_time=bad)
    assert (get("quiet_start"), get("quiet_end"), get("daily_time")) == ("23:15", "05:45", "08:05")


def test_channel_status_shows_configured_flags_never_secrets(safety_app, monkeypatch):
    secret = "SECRET-TOKEN-VALUE-XYZ"
    for k in ("TELEGRAM_BOT_TOKEN", "LINE_CHANNEL_TOKEN", "TEAMS_WEBHOOK_URL", "SMTP_HOST", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    c = login(safety_app)
    html = c.get(URL).get_data(as_text=True)
    assert html.count("ยังไม่ได้ตั้งค่า") >= 4
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret); monkeypatch.setenv("SMTP_HOST", "smtp.secret-host.example")
    monkeypatch.setenv("SMTP_PASSWORD", secret + "2"); monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://hook.example/" + secret)
    html = c.get(URL).get_data(as_text=True)
    assert secret not in html and "smtp.secret-host.example" not in html and "ตั้งค่าแล้ว" in html
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "http://insecure.example/x")
    assert "ต้องเป็น https" in c.get(URL).get_data(as_text=True)


def seed_outbox(app, status, channel="telegram", target="123456789", subject="หัวข้อ", err=""):
    con = connect(app.config["DATABASE"])
    cur = con.execute("INSERT INTO notify_outbox(channel,target,subject,body,status,next_try_at,dedupe_key,last_error,attempts) "
                      "VALUES(?,?,?,?,?, '2026-01-01 00:00:00', ?, ?, 6)", (channel, target, subject, "b", status, f"k{status}{channel}{target}", err))
    con.commit(); con.close()
    return cur.lastrowid


def test_outbox_retry_and_cancel_state_rules(safety_app):
    c = login(safety_app)
    ids = {s: seed_outbox(safety_app, s, target=f"10{i}") for i, s in enumerate(("pending", "failed", "dead", "sent", "cancelled"))}
    status = lambda i: sql(safety_app, "SELECT status FROM notify_outbox WHERE id=?", (i,))[0][0]
    for s in ("sent", "cancelled", "pending"):
        post(c, action="outbox_retry", id=str(ids[s]))
        assert status(ids[s]) == s                                                       # retry ใช้ได้เฉพาะ dead/failed
    post(c, action="outbox_retry", id=str(ids["dead"]))
    row = sql(safety_app, "SELECT * FROM notify_outbox WHERE id=?", (ids["dead"],))[0]
    assert (row["status"], row["attempts"], row["last_error"]) == ("pending", 0, "")
    for s in ("sent", "dead"):
        post(c, action="outbox_cancel", id=str(ids[s]))
    assert status(ids["sent"]) == "sent"
    post(c, action="outbox_cancel", id=str(ids["failed"]))
    post(c, action="outbox_cancel", id=str(ids["pending"]))
    assert status(ids["failed"]) == status(ids["pending"]) == "cancelled"
    post(c, action="outbox_cancel", id="99999")


def test_outbox_view_masks_targets_and_escapes(safety_app):
    c = login(safety_app)
    seed_outbox(safety_app, "dead", "telegram", "123456789", "<script>alert(1)</script>", "Telegram HTTP 403")
    seed_outbox(safety_app, "failed", "line", "U" + "a" * 28 + "WXYZ")
    seed_outbox(safety_app, "dead", "email", "somebody@example.com")
    seed_outbox(safety_app, "dead", "teams", "teams")
    seed_outbox(safety_app, "sent", "telegram", "555000111")
    html = c.get(URL).get_data(as_text=True)
    assert "123456789" not in html and "somebody@example.com" not in html and "a" * 28 not in html
    assert "***789" in html and "s***@example.com" in html and "***WXYZ" in html and "Telegram HTTP 403" in html
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html and "555000111" not in html
    assert "ส่งไม่สำเร็จถาวร" in html and "ล้มเหลว (จะลองใหม่)" in html


def test_send_test_queues_to_own_contacts_only(safety_app):
    c = login(safety_app)
    other = login(safety_app, "reporter")
    con = connect(safety_app.config["DATABASE"])
    con.execute("UPDATE safety_users SET telegram_chat_id='111', email='me@example.com', notify_channels='inapp,telegram,email' WHERE id=?", (c.uid,))
    con.execute("UPDATE safety_users SET telegram_chat_id='999', notify_channels='inapp,telegram' WHERE id=?", (other.uid,))
    con.commit()
    post(c, action="send_test")
    rows = sorted(tuple(r) for r in sql(safety_app, "SELECT channel, target FROM notify_outbox"))
    assert rows == [("email", "me@example.com"), ("telegram", "111")]
    assert sql(safety_app, "SELECT COUNT(*) FROM notifications WHERE user_id=? AND title LIKE '%ทดสอบ%'", (c.uid,))[0][0] == 1
    assert sql(safety_app, "SELECT COUNT(*) FROM notifications WHERE user_id=?", (other.uid,))[0][0] == 0
    post(c, action="send_test")                                                          # กดซ้ำคิวใหม่ได้ (คีย์ต่างกัน)
    assert len(sql(safety_app, "SELECT 1 FROM notify_outbox")) == 4


def test_send_test_without_contacts_explains(safety_app):
    c = login(safety_app)
    post(c, action="send_test")
    assert "โปรไฟล์" in c.get(URL).get_data(as_text=True) and sql(safety_app, "SELECT COUNT(*) FROM notify_outbox")[0][0] == 0


def test_run_daily_now_is_idempotent_for_notifications(safety_app, tmp_path, monkeypatch):
    monkeypatch.setattr("safety.scheduler.backup_database", lambda *a, **k: str(tmp_path / "b.db"))
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    c = login(safety_app)
    owner = make_user(safety_app, "assessor", name="เจ้าของ")
    from safety import timeutil
    from datetime import date, timedelta
    monkeypatch.setattr(timeutil, "today", lambda: date(2026, 3, 18))
    add_hazard(con, ch["a"], c.uid, status="assigned", owner_id=owner, due_date="2026-03-20")                # อีก 2 วัน → due_soon
    post(c, action="run_daily_now")
    n1 = sql(safety_app, "SELECT COUNT(*) FROM notifications")[0][0]
    post(c, action="run_daily_now")
    assert n1 == 1 and sql(safety_app, "SELECT COUNT(*) FROM notifications")[0][0] == n1


def test_script_in_rule_fields_escaped(safety_app):
    c = login(safety_app)
    c.post("/admin/roles", data={"_csrf": csrf(c), "action": "create", "key": "xss_role", "name_th": "<b>x</b>", "permissions": ["master.view"]})
    post(c, **rule_form(recipients="role:xss_role"))
    html = c.get(URL).get_data(as_text=True)
    assert "<b>x</b>" not in html


def test_page_html_has_no_form_directly_under_tr(safety_app):
    from html.parser import HTMLParser
    html = login(safety_app).get(URL).get_data(as_text=True)

    class P(HTMLParser):
        def __init__(self): super().__init__(); self.stack, self.bad = [], 0
        def handle_starttag(self, t, a):
            if t == "form" and self.stack and self.stack[-1] in ("tr", "table"): self.bad += 1
            if t in ("tr", "td", "table", "form"): self.stack.append(t)
        def handle_endtag(self, t):
            if t in ("tr", "td", "table", "form") and t in self.stack:
                while self.stack and self.stack.pop() != t: pass
    p = P(); p.feed(html)
    assert p.bad == 0
