"""ขอสร้างบัญชีเองที่หน้าเข้าสู่ระบบ: เริ่มเป็นรออนุมัติ เข้าไม่ได้จนกว่าผู้ดูแลจะอนุมัติ"""
import pytest

from hub.db import connect
from tests.conftest import csrf
from tests.hub.test_auth import add_user, login
from tests.hub.test_launcher import login_as

FORM = {"username": "newbie", "name_th": "สมหญิง ใจดี", "password": "Passw0rd!", "password2": "Passw0rd!",
        "req_factory": "โรงงานบางนา", "req_department": "ฝ่ายผลิต", "req_line": "ไลน์ประกอบ 1"}


@pytest.fixture(autouse=True)
def no_safety(monkeypatch):
    from hub.views import auth
    monkeypatch.setattr(auth, "fetch_org", lambda *a, **k: None)                                           # Safety ไม่ตอบ → ช่องพิมพ์เอง


def row(app, name="newbie"):
    return connect(app.config["DATABASE"]).execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()


def register(c, **kw):
    return c.post("/register", data={**FORM, **kw, "_csrf": csrf(c)})


def test_register_creates_pending_inactive_viewer_that_cannot_login(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    assert c.get("/register").status_code == 200 and "/register" in c.get("/login").get_data(as_text=True)
    r = register(c)
    assert r.status_code == 200 and "ส่งคำขอแล้ว" in r.get_data(as_text=True)
    u = row(hub_app)
    assert (u["active"], u["pending"], u["role"]) == (0, 1, "viewer")
    assert u["password_hash"] != FORM["password"]
    r = login(c, "newbie", "Passw0rd!")
    assert r.status_code == 200 and "รอผู้ดูแลระบบอนุมัติ" in r.get_data(as_text=True)
    with c.session_transaction() as s:
        assert "uid" not in s


def test_register_validation(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    for bad, msg in (({"username": "a b"}, "ชื่อผู้ใช้"), ({"username": "ab"}, "ชื่อผู้ใช้"), ({"name_th": " "}, "ชื่อ-นามสกุล"),
                     ({"password": "short", "password2": "short"}, "รหัสผ่าน"), ({"password2": "different1"}, "ไม่ตรงกัน"),
                     ({"username": "boss"}, "ถูกใช้แล้ว"), ({"req_factory": ""}, "โรงงานและแผนก"), ({"req_department": " "}, "โรงงานและแผนก")):
        assert msg in register(c, **bad).get_data(as_text=True), bad
    assert row(hub_app) is None


def test_register_redirects_to_setup_before_first_admin(hub_app):
    assert hub_app.test_client().get("/register").status_code == 302


def test_pending_cap(hub_app, monkeypatch):
    add_user(hub_app)
    from hub.views import auth
    monkeypatch.setattr(auth, "MAX_PENDING", 1)
    c = hub_app.test_client()
    register(c)
    assert "มากเกินไป" in register(c, username="second").get_data(as_text=True) and row(hub_app, "second") is None


def test_admin_approves_with_role_then_user_can_login(hub_app):
    admin = login_as(hub_app)
    register(hub_app.test_client())
    page = admin.get("/admin/users").get_data(as_text=True)
    assert "รออนุมัติ (1)" in page and "newbie" in page
    uid = row(hub_app)["id"]
    admin.post("/admin/users", data={"action": "toggle", "id": uid, "_csrf": csrf(admin)})                  # ข้ามขั้นอนุมัติไม่ได้
    admin.post("/admin/users", data={"action": "set_role", "id": uid, "role": "admin", "_csrf": csrf(admin)})
    assert (row(hub_app)["active"], row(hub_app)["pending"], row(hub_app)["role"]) == (0, 1, "viewer")
    admin.post("/admin/users", data={"action": "approve", "id": uid, "role": "technician", "_csrf": csrf(admin)})
    u = row(hub_app)
    assert (u["active"], u["pending"], u["role"]) == (1, 0, "technician")
    c = hub_app.test_client()
    assert login(c, "newbie", "Passw0rd!").status_code == 302
    assert "รออนุมัติ (" not in admin.get("/admin/users").get_data(as_text=True)


def test_admin_rejects_and_only_pending_can_be_rejected(hub_app):
    admin = login_as(hub_app)
    register(hub_app.test_client())
    uid = row(hub_app)["id"]
    me = connect(hub_app.config["DATABASE"]).execute("SELECT id FROM users WHERE username != 'newbie' LIMIT 1").fetchone()[0]
    admin.post("/admin/users", data={"action": "reject", "id": me, "_csrf": csrf(admin)})                   # ลบบัญชีจริงด้วยปุ่มปฏิเสธไม่ได้
    assert connect(hub_app.config["DATABASE"]).execute("SELECT 1 FROM users WHERE id=?", (me,)).fetchone()
    admin.post("/admin/users", data={"action": "reject", "id": uid, "_csrf": csrf(admin)})
    assert row(hub_app) is None


def test_non_admin_cannot_approve(hub_app):
    sup = login_as(hub_app, role="supervisor")
    register(hub_app.test_client())
    r = sup.post("/admin/users", data={"action": "approve", "id": row(hub_app)["id"], "role": "admin", "_csrf": csrf(sup)})
    assert r.status_code == 403 and row(hub_app)["pending"] == 1


def test_pending_user_is_inactive_for_safety_status_api(hub_app):
    add_user(hub_app)
    register(hub_app.test_client())
    c = hub_app.test_client()
    r = c.get(f"/api/user-status/{row(hub_app)['id']}", headers={"X-Service-Token": hub_app.config["SERVICE_TOKEN"]})
    assert r.get_json()["active"] is False


ORG = {"factories": [{"id": 1, "name": "โรงงานบางนา"}, {"id": 2, "name": "โรงงานระยอง"}],
       "departments": [{"id": 10, "name": "ฝ่ายผลิต", "factory_id": 1}, {"id": 20, "name": "ฝ่ายซ่อมบำรุง", "factory_id": 2}],
       "lines": [{"id": 100, "name": "ไลน์ประกอบ 1", "department_id": 10}]}


def test_register_with_org_lists_requires_consistent_choice_and_stores_names(hub_app, monkeypatch):
    from hub.views import auth
    monkeypatch.setattr(auth, "fetch_org", lambda *a, **k: ORG)
    add_user(hub_app)
    c = hub_app.test_client()
    page = c.get("/register").get_data(as_text=True)
    assert "โรงงานระยอง" in page and 'id="f-dept"' in page and "/static/hub/register.js" in page
    base = {k: v for k, v in FORM.items() if not k.startswith("req_")}
    bad = ({"factory_id": "1", "department_id": "20"}, {"factory_id": "1", "department_id": "10", "line_id": "999"}, {"factory_id": "9", "department_id": "10"}, {})
    for extra in bad:
        assert "โรงงานและแผนก" in c.post("/register", data={**base, **extra, "_csrf": csrf(c)}).get_data(as_text=True), extra
    assert row(hub_app) is None
    assert "ส่งคำขอแล้ว" in c.post("/register", data={**base, "factory_id": "1", "department_id": "10", "line_id": "100", "_csrf": csrf(c)}).get_data(as_text=True)
    u = row(hub_app)
    assert (u["req_factory"], u["req_department"], u["req_line"]) == ("โรงงานบางนา", "ฝ่ายผลิต", "ไลน์ประกอบ 1")


def test_pending_request_shows_requested_org_to_admin_and_in_sso_claim(hub_app):
    admin = login_as(hub_app)
    register(hub_app.test_client())
    assert "โรงงานบางนา › ฝ่ายผลิต › ไลน์ประกอบ 1" in admin.get("/admin/users").get_data(as_text=True)


def test_admin_can_close_and_reopen_self_registration(hub_app):
    admin = login_as(hub_app)
    anon = hub_app.test_client()
    assert 'href="/register"' in anon.get("/login").get_data(as_text=True)
    admin.post("/admin/users", data={"action": "set_registration", "open": "0", "_csrf": csrf(admin)})
    assert 'href="/register"' not in anon.get("/login").get_data(as_text=True)
    r = anon.get("/register")
    assert r.status_code == 403 and "ปิดรับคำขอ" in r.get_data(as_text=True)
    assert anon.post("/register", data={**FORM, "_csrf": csrf(anon)}).status_code == 403 and row(hub_app) is None
    admin.post("/admin/users", data={"action": "set_registration", "open": "1", "_csrf": csrf(admin)})
    assert anon.get("/register").status_code == 200


def test_non_admin_cannot_toggle_registration(hub_app):
    sup = login_as(hub_app, role="supervisor")
    assert sup.post("/admin/users", data={"action": "set_registration", "open": "0", "_csrf": csrf(sup)}).status_code == 403


def test_admin_sees_pending_banner_on_launcher_others_do_not(hub_app):
    admin = login_as(hub_app)
    register(hub_app.test_client())
    html = admin.get("/").get_data(as_text=True)
    assert "คำขอสร้างบัญชีรออนุมัติ 1 รายการ" in html and "/admin/users#pending" in html
    viewer = login_as(hub_app, username="v1", role="viewer")
    assert "รออนุมัติ" not in viewer.get("/").get_data(as_text=True)
