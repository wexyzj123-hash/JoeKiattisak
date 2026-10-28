import pytest

from hub.db import connect
from tests.conftest import csrf
from tests.hub.test_launcher import login_as


def rows(app, q, args=()):
    con = connect(app.config["DATABASE"])
    try:
        return con.execute(q, args).fetchall()
    finally:
        con.close()


def post(c, path, /, **data):
    return c.post(path, data={"_csrf": csrf(c), **data})


def app_form(**kw):
    return {"action": "save", "key": "docs", "name_th": "เอกสาร", "url": "http://docs.test",
            "icon": "doc", "required_role": "", "sort": "5", **kw}


@pytest.mark.parametrize("url", ["/admin/apps", "/admin/users"])
def test_admin_pages_forbidden_for_non_admin(hub_app, url):
    c = login_as(hub_app, role="supervisor")
    assert c.get(url).status_code == 403
    assert post(c, url).status_code == 403


def test_add_app_then_visible_on_launcher(hub_app):
    c = login_as(hub_app)
    assert post(c, "/admin/apps", **app_form()).status_code == 302
    assert "เอกสาร" in c.get("/").get_data(as_text=True)
    assert "เอกสาร" in c.get("/admin/apps").get_data(as_text=True)


def test_toggle_app_hides_it(hub_app):
    c = login_as(hub_app)
    post(c, "/admin/apps", **app_form())
    post(c, "/admin/apps", action="toggle", key="docs")
    assert "เอกสาร" not in c.get("/").get_data(as_text=True)


@pytest.mark.parametrize("bad", [{"key": "Bad Key!"}, {"key": "a"}, {"url": "javascript:alert(1)"},
                                 {"url": "ftp://x"}, {"required_role": "root"}, {"name_th": ""}])
def test_rejects_bad_app_input(hub_app, bad):
    c = login_as(hub_app)
    post(c, "/admin/apps", **app_form(**bad))
    assert rows(hub_app, "SELECT 1 FROM app_registry WHERE key NOT IN ('safety')") == []


def test_save_app_updates_existing_key(hub_app):
    c = login_as(hub_app)
    post(c, "/admin/apps", **app_form())
    post(c, "/admin/apps", **app_form(name_th="เอกสารใหม่"))
    r = rows(hub_app, "SELECT name_th FROM app_registry WHERE key='docs'")
    assert [x["name_th"] for x in r] == ["เอกสารใหม่"]


def test_cannot_disable_or_demote_last_admin(hub_app):
    c = login_as(hub_app)
    uid = rows(hub_app, "SELECT id FROM users")[0]["id"]
    post(c, "/admin/users", action="toggle", id=str(uid))
    post(c, "/admin/users", action="set_role", id=str(uid), role="viewer")
    u = rows(hub_app, "SELECT role, active FROM users")[0]
    assert (u["role"], u["active"]) == ("admin", 1)


def test_can_demote_admin_when_another_exists(hub_app):
    c = login_as(hub_app)
    post(c, "/admin/users", action="create", username="two", name_th="สอง", password="Passw0rd!", role="admin")
    two = rows(hub_app, "SELECT id FROM users WHERE username='two'")[0]["id"]
    post(c, "/admin/users", action="set_role", id=str(two), role="viewer")
    assert rows(hub_app, "SELECT role FROM users WHERE username='two'")[0]["role"] == "viewer"


def test_create_user_min_password_8(hub_app):
    c = login_as(hub_app)
    post(c, "/admin/users", action="create", username="u1", name_th="x", password="short", role="viewer")
    assert rows(hub_app, "SELECT 1 FROM users WHERE username='u1'") == []
    post(c, "/admin/users", action="create", username="u1", name_th="x", password="12345678", role="viewer")
    assert len(rows(hub_app, "SELECT 1 FROM users WHERE username='u1'")) == 1


def test_create_user_rejects_duplicate_and_bad_role(hub_app):
    c = login_as(hub_app)
    post(c, "/admin/users", action="create", username="boss", name_th="x", password="12345678", role="viewer")
    post(c, "/admin/users", action="create", username="u2", name_th="x", password="12345678", role="god")
    assert len(rows(hub_app, "SELECT 1 FROM users")) == 1
    assert c.get("/admin/users").status_code == 200


def test_reset_password_enforces_min_and_changes_hash(hub_app):
    c = login_as(hub_app)
    post(c, "/admin/users", action="create", username="u3", name_th="x", password="12345678", role="viewer")
    u = rows(hub_app, "SELECT id, password_hash FROM users WHERE username='u3'")[0]
    post(c, "/admin/users", action="reset_password", id=str(u["id"]), password="short")
    assert rows(hub_app, "SELECT password_hash FROM users WHERE id=?", (u["id"],))[0][0] == u["password_hash"]
    post(c, "/admin/users", action="reset_password", id=str(u["id"]), password="newpass123")
    assert rows(hub_app, "SELECT password_hash FROM users WHERE id=?", (u["id"],))[0][0] != u["password_hash"]
