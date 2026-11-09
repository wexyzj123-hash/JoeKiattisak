"""«หน่วยงานของฉัน»: ผู้ที่ผูกกับแผนกเห็นรายการของแผนกตนได้ทุกรายการ (อ่านอย่างเดียว) แต่ไม่เห็นของแผนกอื่น และหน้าผู้ใช้แก้ในฟอร์มเดียว"""
import pytest

from safety import approvals
from tests.conftest import csrf
from tests.safety.helpers import login_as
from tests.safety.test_approvals import World


@pytest.fixture
def w(safety_app):
    return World(safety_app)


def test_unit_member_can_open_every_item_of_own_department_but_not_others(w):
    h_a, h_b = w.hazard(), w.hazard(w.b)
    mate = login_as(w.app, w.mate)                                                    # บทบาทดูอย่างเดียว ผูกแผนก A
    assert mate.get(f"/hazards/{h_a}").status_code == 200
    assert mate.get(f"/hazards/{h_b}").status_code == 404
    page = mate.get(f"/hazards/{h_a}").get_data(as_text=True)
    assert "น้ำมันหก" in page and 'action="/hazards/' not in page.split("การอนุมัติ")[0]       # ไม่มีปุ่มดำเนินการ (อ่านอย่างเดียว)
    assert login_as(w.app, w.other).get(f"/hazards/{h_a}").status_code == 404
    assert mate.post(f"/hazards/{h_a}/assess", data={"_csrf": csrf(mate), "likelihood": "1", "severity": "1", "measure": "1"}).status_code == 403


def test_unit_page_lists_department_items_with_status_and_approval(w):
    w.on()
    hid = w.hazard()
    w.hazard(w.b)
    c = login_as(w.app, w.mate)
    page = c.get("/unit").get_data(as_text=True)
    assert "แผนก A" in page and f"/hazards/{hid}" in page and "รออนุมัติ" in page
    assert page.count("/hazards/") >= 1 and "น้ำมันหก" in page
    assert 'href="/hazards/2"' not in page                                              # รายการของแผนก B ไม่โผล่
    assert "เรียน ผู้เกี่ยวข้อง" in page                                                  # ประกาศทางการของหน่วยงานอยู่ในหน้านี้


def test_gm_sees_all_units_and_company_wide_user_without_unit_sees_empty_state(w):
    h_a, h_b = w.hazard(), w.hazard(w.b)
    gm = login_as(w.app, w.gm)
    assert gm.get(f"/hazards/{h_a}").status_code == 200 and gm.get(f"/hazards/{h_b}").status_code == 200
    nobody = w._user("viewer", "ไม่ผูกหน่วยงาน", None, None, False)
    page = login_as(w.app, nobody).get("/unit").get_data(as_text=True)
    assert "ยังไม่ได้ผูกกับโรงงานหรือแผนก" in page


def test_home_shows_department_status_panel_and_menu_entry(w):
    w.hazard()
    c = login_as(w.app, w.mate)
    home = c.get("/").get_data(as_text=True)
    assert "สถานะหน่วยงานของฉัน" in home and 'href="/unit"' in home and "หน่วยงานของฉัน" in home


def test_unit_incident_is_visible_to_unit_but_people_stay_private(w):
    from safety import incidents, timeutil
    from tests.safety.factories import user_row
    iid, errors = incidents.report(w.db, user_row(w.db, w.rep), {
        "kind": "accident", "occurred_at": timeutil.now().strftime("%Y-%m-%dT%H:%M"), "risk_area_id": str(w.a["a"]), "title": "รถตักชนเสา",
        "injury_severity_id": str(w.db.execute("SELECT id FROM injury_severities LIMIT 1").fetchone()[0]),
        "people_name_0": "สมชาย ลับมาก", "people_no_0": "E-9999"})
    assert errors == []
    page = login_as(w.app, w.mate).get(f"/incidents/{iid}").get_data(as_text=True)
    assert "รถตักชนเสา" in page and "สมชาย ลับมาก" not in page and "E-9999" not in page
    assert login_as(w.app, w.other).get(f"/incidents/{iid}").status_code == 404


def test_users_page_single_form_saves_role_position_and_scope_and_has_no_level(w):
    admin = login_as(w.app, w.admin)
    html = admin.get("/admin/users").get_data(as_text=True)
    assert "L0" not in html and 'name="level_id"' not in html and 'value="save_user"' in html and 'id="user-filter"' in html
    sv_pos = w.db.execute("SELECT id FROM positions WHERE code='DM'").fetchone()[0]
    r = admin.post("/admin/users", data={"_csrf": csrf(admin), "action": "save_user", "id": w.mate, "role": "assessor", "position_id": sv_pos,
                                         "factory_id": w.b["f"], "department_id": w.b["d"]})
    assert r.status_code == 302
    u = w.db.execute("SELECT role, position_id, factory_id, department_id FROM safety_users WHERE id=?", (w.mate,)).fetchone()
    assert tuple(u) == ("assessor", sv_pos, w.b["f"], w.b["d"])
    bad = admin.post("/admin/users", data={"_csrf": csrf(admin), "action": "save_user", "id": w.mate, "role": "nope", "position_id": "", "factory_id": "", "department_id": ""})
    assert w.db.execute("SELECT role FROM safety_users WHERE id=?", (w.mate,)).fetchone()[0] == "assessor"
    assert login_as(w.app, w.sv).post("/admin/users", data={"action": "save_user", "id": w.mate}).status_code in (400, 403)


def test_admin_edits_email_and_teams_of_each_user_in_the_same_form(w):
    from safety import delivery
    admin = login_as(w.app, w.admin)
    html = admin.get("/admin/users").get_data(as_text=True)
    assert 'name="email"' in html and 'name="teams_webhook"' in html and "ส่งข้อความทดสอบถึงคนนี้" in html
    hook = "https://contoso.webhook.office.com/webhookb2/abc123"
    base = {"_csrf": csrf(admin), "action": "save_user", "id": w.mate, "role": "viewer", "position_id": "", "factory_id": w.a["f"], "department_id": w.a["d"]}
    r = admin.post("/admin/users", data={**base, "email": "mate@company.com", "teams_webhook": hook, "channels": ["email", "teams"]})
    assert r.status_code == 302
    row = w.db.execute("SELECT email, teams_webhook, notify_channels FROM safety_users WHERE id=?", (w.mate,)).fetchone()
    assert tuple(row) == ("mate@company.com", hook, "inapp,email,teams")
    page = admin.get("/admin/users").get_data(as_text=True)
    assert "mate@company.com" in page and "Teams: ตั้งแล้ว" in page
    admin.post("/admin/users", data={**base, "email": "mate@company.com", "teams_webhook": hook, "channels": ["email"]})       # ไม่รับ Teams
    assert w.db.execute("SELECT notify_channels FROM safety_users WHERE id=?", (w.mate,)).fetchone()[0] == "inapp,email"
    for bad in ({"email": "not-an-email"}, {"teams_webhook": "https://evil.example.com/x"}, {"channels": ["email"], "email": ""}, {"channels": ["teams"], "teams_webhook": ""}):
        admin.post("/admin/users", data={**base, "email": "keep@company.com", **bad})
        assert w.db.execute("SELECT email FROM safety_users WHERE id=?", (w.mate,)).fetchone()[0] == "mate@company.com", bad              # ผิด → ไม่เปลี่ยนอะไรเลย
    admin.post("/admin/users", data={**base, "email": "", "teams_webhook": ""})                                                          # ล้างได้
    assert tuple(w.db.execute("SELECT email, teams_webhook, notify_channels FROM safety_users WHERE id=?", (w.mate,)).fetchone()) == (None, None, "inapp")


def test_admin_test_send_to_a_user_reports_real_result(w, monkeypatch):
    from safety import delivery
    admin = login_as(w.app, w.admin)
    sent = []
    monkeypatch.setitem(delivery.REAL, "email", lambda target, subject, body, env: sent.append(target))
    w.db.execute("UPDATE safety_users SET email='mate@company.com' WHERE id=?", (w.mate,)); w.db.commit()
    admin.post("/admin/users", data={"_csrf": csrf(admin), "action": "test_contact", "id": w.mate})
    assert sent == ["mate@company.com"]
    monkeypatch.setitem(delivery.REAL, "email", lambda *a: (_ for _ in ()).throw(delivery.TransportError("SMTP: เชื่อมต่อไม่สำเร็จหรือหมดเวลา")))
    admin.post("/admin/users", data={"_csrf": csrf(admin), "action": "test_contact", "id": w.mate})
    with admin.session_transaction() as s:
        msgs = [m for _, m in s.get("_flashes", [])]
    assert any("ส่งไม่สำเร็จ" in m and "SMTP" in m for m in msgs)


def test_create_user_with_email_and_teams_and_ordinary_user_cannot_use_it(w):
    admin = login_as(w.app, w.admin)
    hook = "https://contoso.webhook.office.com/webhookb2/zzz"
    admin.post("/admin/users", data={"_csrf": csrf(admin), "action": "create", "username": "newuser", "name": "ผู้ใช้ใหม่", "password": "Passw0rd-123", "role": "viewer",
                                     "email": "new@company.com", "teams_webhook": hook, "channels": ["email", "teams"]})
    row = w.db.execute("SELECT email, teams_webhook, notify_channels FROM safety_users WHERE username='newuser'").fetchone()
    assert tuple(row) == ("new@company.com", hook, "inapp,email,teams")
    assert login_as(w.app, w.mate).post("/admin/users", data={"action": "save_user", "id": w.rep, "email": "x@y.zz"}).status_code in (400, 403)
