from safety.db import connect
from safety.perms import PERMISSIONS, user_permissions
from tests.conftest import csrf
from tests.safety.helpers import login, make_user, sql


def perms_of(app, role):
    uid = make_user(app, role)
    con = connect(app.config["DATABASE"])
    try:
        user = con.execute("SELECT * FROM safety_users WHERE id=?", (uid,)).fetchone()
        return user_permissions(con, user)
    finally:
        con.close()


def post(c, path, **data):
    return c.post(path, data={"_csrf": csrf(c), **data})


def test_admin_has_all_permissions(safety_app):
    assert perms_of(safety_app, "admin") == set(PERMISSIONS)


def test_admin_keeps_all_even_if_row_is_tampered(safety_app):
    sql(safety_app, "UPDATE safety_roles SET permissions='' WHERE key='admin'")
    assert perms_of(safety_app, "admin") == set(PERMISSIONS)


def test_viewer_lacks_master_edit_and_unknown_role_has_none(safety_app):
    assert "master.edit" not in perms_of(safety_app, "viewer") and "master.view" in perms_of(safety_app, "viewer")
    assert perms_of(safety_app, "ghost") == set()


def test_permission_required_403_and_anonymous_redirect(safety_app):
    assert safety_app.test_client().get("/admin/roles").headers["Location"] == "http://hub.test"
    assert login(safety_app, "viewer").get("/admin/roles").status_code == 403


def test_roles_page_forbidden_without_roles_manage(safety_app):
    c = login(safety_app, "assessor")
    assert c.get("/admin/roles").status_code == 403
    assert post(c, "/admin/roles", action="create", key="x1", name_th="x").status_code == 403


def test_create_role_with_permissions(safety_app):
    c = login(safety_app)
    r = post(c, "/admin/roles", action="create", key="shift_lead", name_th="หัวหน้ากะ",
             permissions=["master.view", "hazard.report"])
    assert r.status_code == 302
    row = sql(safety_app, "SELECT * FROM safety_roles WHERE key='shift_lead'")[0]
    assert (row["name_th"], row["permissions"], row["is_system"]) == ("หัวหน้ากะ", "master.view,hazard.report", 0)
    assert "หัวหน้ากะ" in c.get("/admin/roles").get_data(as_text=True)


def test_bad_role_key_or_name_rejected(safety_app):
    c = login(safety_app)
    for key in ("A", "1abc", "has space", "x" * 33, "admin"):
        post(c, "/admin/roles", action="create", key=key, name_th="x")
    post(c, "/admin/roles", action="create", key="okay", name_th="")
    assert len(sql(safety_app, "SELECT 1 FROM safety_roles")) == 5


def test_unknown_permission_rejected(safety_app):
    c = login(safety_app)
    post(c, "/admin/roles", action="create", key="evil", name_th="x", permissions=["master.view", "root.all"])
    assert sql(safety_app, "SELECT 1 FROM safety_roles WHERE key='evil'") == []


def test_update_role_permissions(safety_app):
    c = login(safety_app)
    post(c, "/admin/roles", action="update", key="viewer", name_th="ผู้ดู", permissions=["master.view", "hazard.report"])
    r = sql(safety_app, "SELECT name_th, permissions FROM safety_roles WHERE key='viewer'")[0]
    assert (r["name_th"], r["permissions"]) == ("ผู้ดู", "master.view,hazard.report")


def test_admin_role_permissions_cannot_be_reduced(safety_app):
    c = login(safety_app)
    post(c, "/admin/roles", action="update", key="admin", name_th="ผู้ดูแลระบบ", permissions=["master.view"])
    p = sql(safety_app, "SELECT permissions FROM safety_roles WHERE key='admin'")[0][0]
    assert set(p.split(",")) == set(PERMISSIONS)


def test_cannot_delete_system_role(safety_app):
    c = login(safety_app)
    post(c, "/admin/roles", action="delete", key="viewer")
    assert len(sql(safety_app, "SELECT 1 FROM safety_roles WHERE key='viewer'")) == 1


def test_cannot_delete_role_in_use_but_can_delete_unused_custom(safety_app):
    c = login(safety_app)
    post(c, "/admin/roles", action="create", key="temp_role", name_th="ชั่วคราว", permissions=["master.view"])
    make_user(safety_app, "temp_role")
    post(c, "/admin/roles", action="delete", key="temp_role")
    assert len(sql(safety_app, "SELECT 1 FROM safety_roles WHERE key='temp_role'")) == 1
    sql(safety_app, "UPDATE safety_users SET role='viewer' WHERE role='temp_role'")
    post(c, "/admin/roles", action="delete", key="temp_role")
    assert sql(safety_app, "SELECT 1 FROM safety_roles WHERE key='temp_role'") == []
