import pytest

from tests.conftest import csrf
from tests.safety.helpers import login, make_user, sql
from tests.safety.test_sso_login import token


def post(c, **data):
    return c.post("/admin/users", data={"_csrf": csrf(c), **data})


def role_of(app, uid):
    return sql(app, "SELECT role FROM safety_users WHERE id=?", (uid,))[0][0]


def org(app, c):
    """โรงงาน 2 แห่ง แห่งละ 1 แผนก — คืน (f1, f2, d1, d2)"""
    ids = []
    for code in ("F1", "F2"):
        c.post("/master/factories/new", data={"_csrf": csrf(c), "code": code, "name_th": f"โรงงาน {code}"})
    f1, f2 = (sql(app, "SELECT id FROM factories WHERE code=?", (k,))[0][0] for k in ("F1", "F2"))
    for code, fid in (("D1", f1), ("D2", f2)):
        c.post("/master/departments/new", data={"_csrf": csrf(c), "code": code, "name_th": f"แผนก {code}", "factory_id": fid})
    d1, d2 = (sql(app, "SELECT id FROM departments WHERE code=?", (k,))[0][0] for k in ("D1", "D2"))
    return f1, f2, d1, d2


def test_users_page_requires_users_manage(safety_app):
    assert login(safety_app, "assessor").get("/admin/users").status_code == 403
    assert post(login(safety_app, "viewer"), action="toggle", id="1").status_code == 403
    assert safety_app.test_client().get("/admin/users").status_code == 302
    c = login(safety_app, name="สมศรี ทำดี")
    html = c.get("/admin/users").get_data(as_text=True)
    assert "สมศรี ทำดี" in html and "ผู้ดูแลระบบ" in html


def test_set_role_ok_and_unknown_role_rejected(safety_app):
    c = login(safety_app)
    uid = make_user(safety_app, "reporter")
    post(c, action="set_role", id=str(uid), role="assessor")
    assert role_of(safety_app, uid) == "assessor"
    for bad in ("ghost", "", "ADMIN "):
        post(c, action="set_role", id=str(uid), role=bad)
    assert role_of(safety_app, uid) == "assessor"


def test_custom_role_can_be_assigned(safety_app):
    c = login(safety_app)
    c.post("/admin/roles", data={"_csrf": csrf(c), "action": "create", "key": "shift_lead", "name_th": "หัวหน้ากะ",
                                 "permissions": ["master.view"]})
    uid = make_user(safety_app, "reporter")
    post(c, action="set_role", id=str(uid), role="shift_lead")
    assert role_of(safety_app, uid) == "shift_lead"


def test_cannot_demote_or_disable_last_admin(safety_app):
    c = login(safety_app)
    post(c, action="set_role", id=str(c.uid), role="viewer")
    post(c, action="toggle", id=str(c.uid))
    row = sql(safety_app, "SELECT role, active FROM safety_users WHERE id=?", (c.uid,))[0]
    assert (row["role"], row["active"]) == ("admin", 1)


def test_inactive_admin_does_not_count_as_a_second_admin(safety_app):
    c = login(safety_app)
    make_user(safety_app, "admin", active=0)
    post(c, action="set_role", id=str(c.uid), role="viewer")
    assert role_of(safety_app, c.uid) == "admin"


def test_can_demote_admin_when_another_exists(safety_app):
    c = login(safety_app)
    other = make_user(safety_app, "admin")
    post(c, action="set_role", id=str(other), role="viewer")
    assert role_of(safety_app, other) == "viewer"
    post(c, action="set_role", id=str(c.uid), role="viewer")            # ตอนนี้เหลือแอดมินคนเดียว = ตัวเอง
    assert role_of(safety_app, c.uid) == "admin"


def test_set_scope_department_must_match_factory(safety_app):
    c = login(safety_app)
    f1, f2, d1, d2 = org(safety_app, c)
    uid = make_user(safety_app, "dept_head")
    post(c, action="set_scope", id=str(uid), factory_id=str(f1), department_id=str(d2))      # แผนกของโรงงานอื่น
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (None, None)
    post(c, action="set_scope", id=str(uid), factory_id=str(f1), department_id=str(d1))
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (f1, d1)


def test_set_scope_department_only_infers_factory_and_scope_can_be_cleared(safety_app):
    c = login(safety_app)
    f1, f2, d1, d2 = org(safety_app, c)
    uid = make_user(safety_app, "dept_head")
    post(c, action="set_scope", id=str(uid), factory_id="", department_id=str(d2))
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (f2, d2)
    post(c, action="set_scope", id=str(uid), factory_id="", department_id="")
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (None, None)


def test_set_scope_nonexistent_ids_rejected(safety_app):
    c = login(safety_app)
    uid = make_user(safety_app, "dept_head")
    for f, d in (("999", ""), ("", "999"), ("abc", "")):
        post(c, action="set_scope", id=str(uid), factory_id=f, department_id=d)
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (None, None)


def test_unknown_user_or_action_is_harmless(safety_app):
    c = login(safety_app)
    assert post(c, action="toggle", id="999").status_code == 302
    assert post(c, action="explode", id=str(c.uid)).status_code == 302
    assert post(c, action="toggle", id="abc").status_code == 302


def test_toggle_user_blocks_next_sso_login(safety_app):
    c = login(safety_app)
    uid = make_user(safety_app, "reporter", hub_uid=77)
    post(c, action="toggle", id=str(uid))
    assert sql(safety_app, "SELECT active FROM safety_users WHERE id=?", (uid,))[0][0] == 0
    assert safety_app.test_client().get("/sso?token=" + token(uid=77, roles=("viewer",))).status_code == 403
    post(c, action="toggle", id=str(uid))
    assert safety_app.test_client().get("/sso?token=" + token(uid=77, roles=("viewer",))).status_code == 302
