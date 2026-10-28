import re

import pytest

from safety.db import connect
from tests.conftest import csrf
from tests.safety.helpers import login, sql


def add_note(app, uid, n=1, title="เรื่อง", link="/hazards/1", read=False, body="รายละเอียด", prefix="k"):
    con = connect(app.config["DATABASE"])
    ids = []
    for i in range(n):
        cur = con.execute("INSERT INTO notifications(user_id,event,title,body,link,dedupe_key,read_at) VALUES(?,?,?,?,?,?,?)",
                          (uid, "hazard_reported", f"{title} {i}" if n > 1 else title, body, link, f"{prefix}{uid}-{i}-{title}-{link}",
                           "2026-03-10 10:00:00" if read else None))
        ids.append(cur.lastrowid)
    con.commit()
    con.close()
    return ids


def unread(app, uid):
    return sql(app, "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (uid,))[0][0]


def test_anonymous_redirects_to_hub(safety_app):
    c = safety_app.test_client()
    for path in ("/notifications", "/notifications/unread-count", "/profile", "/notifications/go/1"):
        assert c.get(path).status_code == 302, path
    assert c.post("/notifications/read-all", data={}).status_code == 400                       # ไม่มี CSRF


def test_list_shows_only_own_and_newest_first(safety_app):
    me, other = login(safety_app, "reporter"), login(safety_app, "reporter")
    add_note(safety_app, me.uid, 3, title="ของฉัน"); add_note(safety_app, other.uid, 2, title="ของคนอื่น")
    html = me.get("/notifications").get_data(as_text=True)
    assert "ของฉัน 0" in html and "ของคนอื่น" not in html
    assert html.index("ของฉัน 2") < html.index("ของฉัน 1") < html.index("ของฉัน 0")


def test_unread_filter_and_pagination_clamped(safety_app):
    me = login(safety_app, "reporter")
    add_note(safety_app, me.uid, 25, title="ใหม่"); add_note(safety_app, me.uid, 2, title="ประวัติเก่า", read=True, prefix="r")
    page1 = me.get("/notifications").get_data(as_text=True)
    assert len(re.findall(r'class="card note', page1)) == 20
    for qs in ("page=0", "page=-3", "page=999", "page=abc", "unread=1&page=2", "unread=banana"):
        assert me.get("/notifications?" + qs).status_code == 200
    only_unread = me.get("/notifications?unread=1&page=2").get_data(as_text=True)
    assert "ประวัติเก่า" not in only_unread and "ใหม่ 0" in only_unread                           # หน้า 2 ของที่ยังไม่อ่าน (5 รายการ)


def test_mark_read_and_mark_all_read_only_affect_own(safety_app):
    me, other = login(safety_app, "reporter"), login(safety_app, "reporter")
    mine = add_note(safety_app, me.uid, 3); theirs = add_note(safety_app, other.uid, 2)
    assert me.post(f"/notifications/{mine[0]}/read", data={"_csrf": csrf(me)}).status_code == 302
    assert unread(safety_app, me.uid) == 2 and unread(safety_app, other.uid) == 2
    me.post("/notifications/read-all", data={"_csrf": csrf(me)})
    assert unread(safety_app, me.uid) == 0 and unread(safety_app, other.uid) == 2


def test_other_users_notification_is_404(safety_app):
    me, other = login(safety_app, "reporter"), login(safety_app, "reporter")
    theirs = add_note(safety_app, other.uid)[0]
    assert me.post(f"/notifications/{theirs}/read", data={"_csrf": csrf(me)}).status_code == 404
    assert me.get(f"/notifications/go/{theirs}").status_code == 404
    assert me.get("/notifications/go/99999").status_code == 404
    assert unread(safety_app, other.uid) == 1


def test_go_redirects_to_internal_link_and_marks_read(safety_app):
    me = login(safety_app, "reporter")
    nid = add_note(safety_app, me.uid, link="/hazards/42")[0]
    r = me.get(f"/notifications/go/{nid}")
    assert r.status_code == 302 and r.headers["Location"] == "/hazards/42" and unread(safety_app, me.uid) == 0


@pytest.mark.parametrize("link", ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)", "", "/ok\r\nX: y", "evil"])
def test_go_never_redirects_off_site(safety_app, link):
    me = login(safety_app, "reporter")
    nid = add_note(safety_app, me.uid, link=link)[0]
    r = me.get(f"/notifications/go/{nid}")
    assert r.status_code == 302 and r.headers["Location"] == "/notifications" and unread(safety_app, me.uid) == 0


def test_unread_count_endpoint_and_bell_badge(safety_app):
    me = login(safety_app, "reporter")
    assert me.get("/notifications/unread-count").get_json() == {"count": 0}
    add_note(safety_app, me.uid, 3)
    assert me.get("/notifications/unread-count").get_json() == {"count": 3}
    html = me.get("/hazards").get_data(as_text=True)
    assert 'id="bell-badge"' in html and ">3<" in html and "/notifications/unread-count" in html
    me.post("/notifications/read-all", data={"_csrf": csrf(me)})
    assert 'id="bell-badge"' in me.get("/hazards").get_data(as_text=True) and me.get("/notifications/unread-count").get_json() == {"count": 0}


def test_bell_link_and_profile_link_in_sidebar_for_every_role(safety_app):
    for role in ("reporter", "viewer", "admin"):
        html = login(safety_app, role).get("/").get_data(as_text=True)
        assert 'href="/notifications"' in html and 'href="/profile"' in html


def test_xss_in_notification_text_escaped(safety_app):
    me = login(safety_app, "reporter")
    add_note(safety_app, me.uid, title="<script>alert(1)</script>", body='"><img src=x onerror=alert(1)>')
    html = me.get("/notifications").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_empty_state_renders(safety_app):
    html = login(safety_app, "reporter").get("/notifications").get_data(as_text=True)
    assert "ยังไม่มีการแจ้งเตือน" in html


def test_profile_shows_and_saves_contacts_and_opt_in(safety_app):
    me = login(safety_app, "reporter")
    assert me.get("/profile").status_code == 200
    r = me.post("/profile", data={"_csrf": csrf(me), "telegram_chat_id": " 123456 ", "line_user_id": "U" + "a" * 32,
                                  "email": "me@example.com", "channels": ["telegram", "email"]})
    assert r.status_code == 302
    row = sql(safety_app, "SELECT telegram_chat_id, line_user_id, email, notify_channels FROM safety_users WHERE id=?", (me.uid,))[0]
    assert tuple(row) == ("123456", "U" + "a" * 32, "me@example.com", "inapp,telegram,email")
    html = me.get("/profile").get_data(as_text=True)
    assert 'value="123456"' in html and "me@example.com" in html and re.search(r'name="channels" value="telegram"[^>]*checked', html)


@pytest.mark.parametrize("field, bad", [("telegram_chat_id", "abc"), ("telegram_chat_id", "1" * 30), ("line_user_id", "u" + "a" * 32),
                                        ("line_user_id", "U123"), ("email", "no-at"), ("email", "a b@c.d"), ("email", "a@b.c\r\nBcc: x@y.z")])
def test_profile_rejects_bad_contacts_and_keeps_old_values(safety_app, field, bad):
    me = login(safety_app, "reporter")
    me.post("/profile", data={"_csrf": csrf(me), "email": "keep@example.com", "channels": ["email"]})
    r = me.post("/profile", data={"_csrf": csrf(me), "email": "keep@example.com", field: bad, "channels": ["email"]})
    assert r.status_code == 200 and "ไม่ถูกต้อง" in r.get_data(as_text=True)
    assert sql(safety_app, "SELECT email FROM safety_users WHERE id=?", (me.uid,))[0][0] == "keep@example.com"
    assert sql(safety_app, f"SELECT {field} FROM safety_users WHERE id=?", (me.uid,))[0][0] in (None, "keep@example.com")


def test_profile_channel_needs_contact_and_clearing_allowed(safety_app):
    me = login(safety_app, "reporter")
    r = me.post("/profile", data={"_csrf": csrf(me), "channels": ["telegram"]})
    assert r.status_code == 200 and "กรุณากรอก" in r.get_data(as_text=True)
    me.post("/profile", data={"_csrf": csrf(me), "email": "x@example.com", "channels": ["email"]})
    me.post("/profile", data={"_csrf": csrf(me), "email": "", "telegram_chat_id": "", "line_user_id": ""})
    row = sql(safety_app, "SELECT telegram_chat_id, line_user_id, email, notify_channels FROM safety_users WHERE id=?", (me.uid,))[0]
    assert tuple(row) == (None, None, None, "inapp")


def test_profile_cannot_change_role_or_scope_or_unknown_channels(safety_app):
    me = login(safety_app, "reporter")
    me.post("/profile", data={"_csrf": csrf(me), "role": "admin", "factory_id": "1", "department_id": "1", "active": "0", "name": "แฮ็ก",
                              "channels": ["inapp", "teams", "sms", "telegram"], "telegram_chat_id": "5"})
    row = sql(safety_app, "SELECT role, factory_id, department_id, active, name, notify_channels FROM safety_users WHERE id=?", (me.uid,))[0]
    assert tuple(row) == ("reporter", None, None, 1, "ทดสอบ", "inapp,telegram")


def test_profile_xss_escaped(safety_app):
    me = login(safety_app, "reporter", name="<b>x</b>")
    me.post("/profile", data={"_csrf": csrf(me), "email": '"><script>1</script>@x.co'})
    html = me.get("/profile").get_data(as_text=True)
    assert "<script>1" not in html and "<b>x</b>" not in html
