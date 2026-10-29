"""ขอสร้างบัญชีเองที่หน้าเข้าสู่ระบบ: เริ่มเป็นรออนุมัติ เข้าไม่ได้จนกว่าผู้ดูแลจะอนุมัติ"""
from hub.db import connect
from tests.conftest import csrf
from tests.hub.test_auth import add_user, login
from tests.hub.test_launcher import login_as

FORM = {"username": "newbie", "name_th": "สมหญิง ใจดี", "password": "Passw0rd!", "password2": "Passw0rd!"}


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
                     ({"username": "boss"}, "ถูกใช้แล้ว")):
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
