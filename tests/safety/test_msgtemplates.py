"""แต่งข้อความแจ้งเตือนมาตรฐานโดยผู้ดูแล: แม่แบบ {ตัวแปร} + [[ส่วนเสริม]], ตรวจความถูกต้อง, ใช้จริงกับทุกช่องทาง, คืนค่ามาตรฐานได้"""
import pytest

from safety import msgtemplates, notify, workflow
from safety.db import connect
from tests.conftest import csrf
from tests.safety.helpers import login_as
from tests.safety.test_approvals import World

URL = "/admin/message-templates"


@pytest.fixture
def w(safety_app):
    return World(safety_app)


def post(c, **kw):
    return c.post(URL, data={"_csrf": csrf(c), "action": "save", **kw})


def test_fill_substitutes_and_drops_optional_parts_with_empty_values():
    vals = {"code": "HZ-1", "title": "น้ำมันหก", "area": "", "due": "2026-11-20"}
    assert msgtemplates.fill("{title}[[ · พื้นที่ {area}]][[ · ครบกำหนด {due}]]", vals) == "น้ำมันหก · ครบกำหนด 2026-11-20"
    assert msgtemplates.fill("{unknown} {code}", vals) == "{unknown} HZ-1"


def test_every_default_uses_only_allowed_variables_and_validates():
    for event, (title, body) in msgtemplates.DEFAULTS.items():
        assert msgtemplates.validate(event, title, body) == [], event
    assert set(msgtemplates.DEFAULTS) == set(notify.NOTIFY_EVENTS)


@pytest.mark.parametrize("title, body, fragment", [
    ("", "x", "ชื่อเรื่อง"), ("t", "", "เนื้อหา"), ("{nope}", "x", "ตัวแปร"), ("t", "{code} {days_left}", "ตัวแปร"),
    ("t", "[[ {code}", "ส่วนเสริม"), ("t", "{code}]]", "ส่วนเสริม"), ("t", "{code", "วงเล็บปีกกา"), ("two\nlines", "x", "บรรทัดเดียว"), ("t", "a\x00b", "อักขระควบคุม"),
    ("t" * 400, "x", "ยาวเกิน"), ("t", "x" * 3100, "ยาวเกิน")])
def test_validation_rejects_bad_templates(title, body, fragment):
    errors = msgtemplates.validate("hazard_reported", title, body)
    assert errors and any(fragment in e for e in errors), errors


def test_admin_edits_message_and_next_notification_uses_it_on_every_channel(w):
    admin = login_as(w.app, w.admin)
    r = post(admin, event="hazard_reported", title="[ด่วน] พบอันตราย {code}", body="เรียน ทีมงาน\nมีเรื่อง {title}[[ ที่ {area}]] แจ้งโดย {reporter}\nขอบคุณ")
    assert r.status_code == 302
    from tests.safety.factories import add_user
    asr = add_user(w.db, "assessor", "ผู้ประเมินแผนก", factory_id=w.a["f"], department_id=w.a["d"])
    hid = w.hazard()
    row = w.db.execute("SELECT title, body FROM notifications WHERE hazard_id=? AND event='hazard_reported' LIMIT 1", (hid,)).fetchone()
    assert row["title"].startswith("[ด่วน] พบอันตราย HZ-") and row["body"].startswith("เรียน ทีมงาน\nมีเรื่อง น้ำมันหก ที่ ")
    assert "แจ้งโดย ผู้แจ้ง" in row["body"] and row["body"].endswith("\nขอบคุณ")
    w.db.execute("UPDATE safety_users SET email='a@b.co', notify_channels='inapp,email' WHERE id=?", (w.sv,)); w.db.commit()
    from safety import delivery
    assert "เรียน ทีมงาน\nมีเรื่อง" in delivery._text(row["title"], row["body"], "teams")                       # ขึ้นบรรทัดใหม่ถึงปลายทาง
    page = admin.get(URL).get_data(as_text=True)
    assert "แก้ไขแล้ว" in page and "คืนค่ามาตรฐาน" in page


def test_saving_the_default_text_stores_nothing_and_reset_restores_it(w):
    admin = login_as(w.app, w.admin)
    t, b = msgtemplates.DEFAULTS["hazard_closed"]
    post(admin, event="hazard_closed", title=t, body=b)
    assert w.db.execute("SELECT COUNT(*) FROM notify_templates").fetchone()[0] == 0
    post(admin, event="hazard_closed", title="ปิดแล้ว {code}", body="{title}")
    assert w.db.execute("SELECT COUNT(*) FROM notify_templates").fetchone()[0] == 1
    admin.post(URL, data={"_csrf": csrf(admin), "action": "reset", "event": "hazard_closed"})
    assert w.db.execute("SELECT COUNT(*) FROM notify_templates").fetchone()[0] == 0
    assert msgtemplates.get(w.db, "hazard_closed") == (t, b)


def test_invalid_input_is_rejected_nothing_saved_and_non_admin_blocked(w):
    admin = login_as(w.app, w.admin)
    for bad in ({"title": "{bogus}", "body": "x"}, {"title": "", "body": "x"}, {"title": "t", "body": "{code"}):
        post(admin, event="hazard_assigned", **bad)
    post(admin, event="no_such_event", title="t", body="b")
    assert w.db.execute("SELECT COUNT(*) FROM notify_templates").fetchone()[0] == 0
    sv = login_as(w.app, w.sv)
    assert sv.get(URL).status_code == 403 and sv.post(URL, data={"event": "hazard_reported", "title": "t", "body": "b"}).status_code in (400, 403)
    assert w.app.test_client().get(URL).status_code == 302
    assert admin.post(URL, data={"event": "hazard_reported", "title": "t", "body": "b", "action": "save"}).status_code == 400      # ไม่มี CSRF


def test_approval_and_unit_notices_follow_edited_text(w):
    admin = login_as(w.app, w.admin)
    post(admin, event="unit_hazard_notice", title="ประกาศ {code}", body="เรียน พี่น้องชาว{area}\nพบ {title} เมื่อ {when}\nขอบคุณครับ")
    hid = w.hazard()
    row = w.db.execute("SELECT title, body FROM notifications WHERE hazard_id=? AND event='unit_hazard_notice' LIMIT 1", (hid,)).fetchone()
    assert row["title"].startswith("ประกาศ HZ-") and row["body"].startswith("เรียน พี่น้องชาว") and row["body"].endswith("ขอบคุณครับ")
    w.on()
    post(admin, event="approval_requested", title="รอท่านอนุมัติ {code}", body="ขั้น {step} ({approvers}) — {title}")
    hid2 = w.hazard()
    row = w.db.execute("SELECT title, body FROM notifications WHERE hazard_id=? AND event='approval_requested' LIMIT 1", (hid2,)).fetchone()
    assert row["title"].startswith("รอท่านอนุมัติ HZ-") and "ขั้น 1 (SV)" in row["body"]


def test_page_lists_every_event_with_variables_and_preview(w):
    html = login_as(w.app, w.admin).get(URL).get_data(as_text=True)
    for event in msgtemplates.DEFAULTS:
        assert f'id="ev-{event}"' in html, event
    assert "{code}" in html and "data-preview-body" in html and "[[ · ครบกำหนด {due}]]" in html


def test_incident_templates_have_no_personal_variables():
    assert not ({"people", "injured", "employee", "name"} & set(msgtemplates.allowed("incident_reported")))
