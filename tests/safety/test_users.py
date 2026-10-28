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


# ---- ผู้ถือ users.manage / roles.manage ต้องไม่ยกระดับตัวเองเป็นแอดมินได้ ----
def custom_role(app, admin_client, key, perms):
    admin_client.post("/admin/roles", data={"_csrf": csrf(admin_client), "action": "create", "key": key,
                                            "name_th": key, "permissions": perms})


def test_users_manage_cannot_promote_self_or_others_to_admin(safety_app):
    adm = login(safety_app)
    custom_role(safety_app, adm, "hr", ["users.manage"])
    hr = login(safety_app, "hr")
    post(hr, action="set_role", id=str(hr.uid), role="admin")
    other = make_user(safety_app, "reporter")
    post(hr, action="set_role", id=str(other), role="admin")
    assert role_of(safety_app, hr.uid) == "hr" and role_of(safety_app, other) == "reporter"


def test_users_manage_cannot_touch_an_existing_admin(safety_app):
    adm = login(safety_app)
    other_admin = make_user(safety_app, "admin")
    custom_role(safety_app, adm, "hr", ["users.manage"])
    hr = login(safety_app, "hr")
    post(hr, action="toggle", id=str(other_admin))
    post(hr, action="set_role", id=str(other_admin), role="viewer")
    row = sql(safety_app, "SELECT role, active FROM safety_users WHERE id=?", (other_admin,))[0]
    assert (row["role"], row["active"]) == ("admin", 1)


def test_users_manage_can_still_manage_ordinary_users(safety_app):
    adm = login(safety_app)
    custom_role(safety_app, adm, "hr", ["users.manage", "master.view", "hazard.report"])
    hr = login(safety_app, "hr")
    uid = make_user(safety_app, "reporter")
    post(hr, action="set_role", id=str(uid), role="viewer")
    assert role_of(safety_app, uid) == "viewer"
    post(hr, action="toggle", id=str(uid))
    assert sql(safety_app, "SELECT active FROM safety_users WHERE id=?", (uid,))[0][0] == 0


def test_cannot_assign_role_with_permissions_you_lack(safety_app):
    adm = login(safety_app)
    custom_role(safety_app, adm, "hr", ["users.manage"])
    hr = login(safety_app, "hr")
    uid = make_user(safety_app, "reporter")
    post(hr, action="set_role", id=str(uid), role="assessor")           # assessor มีสิทธิ์ที่ hr ไม่มี
    assert role_of(safety_app, uid) == "reporter"


def test_roles_manage_cannot_grant_permissions_it_lacks(safety_app):
    adm = login(safety_app)
    custom_role(safety_app, adm, "rm", ["roles.manage", "master.view"])
    rm = login(safety_app, "rm")
    rm.post("/admin/roles", data={"_csrf": csrf(rm), "action": "update", "key": "rm", "name_th": "rm",
                                  "permissions": ["roles.manage", "users.manage", "master.edit"]})
    assert set(sql(safety_app, "SELECT permissions FROM safety_roles WHERE key='rm'")[0][0].split(",")) == \
        {"roles.manage", "master.view"}
    rm.post("/admin/roles", data={"_csrf": csrf(rm), "action": "create", "key": "sneaky", "name_th": "x",
                                  "permissions": ["users.manage"]})
    assert sql(safety_app, "SELECT 1 FROM safety_roles WHERE key='sneaky'") == []


def test_roles_manage_can_grant_what_it_has_and_cannot_edit_or_delete_higher_roles(safety_app):
    adm = login(safety_app)
    custom_role(safety_app, adm, "rm", ["roles.manage", "master.view", "hazard.report"])
    rm = login(safety_app, "rm")
    rm.post("/admin/roles", data={"_csrf": csrf(rm), "action": "update", "key": "viewer", "name_th": "ผู้ดู",
                                  "permissions": ["master.view", "hazard.report"]})
    assert sql(safety_app, "SELECT permissions FROM safety_roles WHERE key='viewer'")[0][0] == "master.view,hazard.report"
    rm.post("/admin/roles", data={"_csrf": csrf(rm), "action": "update", "key": "assessor", "name_th": "ถูกแก้",
                                  "permissions": ["master.view"]})                       # assessor สูงกว่า rm
    rm.post("/admin/roles", data={"_csrf": csrf(rm), "action": "update", "key": "admin", "name_th": "ถูกแก้"})
    assert sql(safety_app, "SELECT name_th FROM safety_roles WHERE key='assessor'")[0][0] == "ผู้ประเมิน/จป."
    assert sql(safety_app, "SELECT name_th FROM safety_roles WHERE key='admin'")[0][0] == "ผู้ดูแลระบบ"


# ---- ขอบเขตผู้ใช้: ห้ามเลือกที่ปิดอยู่ และห้ามขยายขอบเขตเงียบ ๆ ----
def test_set_scope_rejects_inactive_factory_and_department(safety_app):
    c = login(safety_app)
    f1, f2, d1, d2 = org(safety_app, c)
    c.post(f"/master/factories/{f2}/toggle", data={"_csrf": csrf(c)})
    c.post(f"/master/departments/{d1}/toggle", data={"_csrf": csrf(c)})
    uid = make_user(safety_app, "dept_head")
    post(c, action="set_scope", id=str(uid), factory_id=str(f2), department_id="")
    post(c, action="set_scope", id=str(uid), factory_id="", department_id=str(d1))
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (None, None)


def test_scope_pointing_at_a_later_deactivated_factory_stays_selected_and_resubmittable(safety_app):
    c = login(safety_app)
    f1, f2, d1, d2 = org(safety_app, c)
    uid = make_user(safety_app, "dept_head")
    post(c, action="set_scope", id=str(uid), factory_id=str(f1), department_id=str(d1))
    c.post(f"/master/factories/{f1}/toggle", data={"_csrf": csrf(c)})
    html = c.get("/admin/users").get_data(as_text=True)
    assert f'<option value="{f1}" selected>โรงงาน F1</option>' in html              # ตัวเลือกโรงงาน (ไม่ปนกับตัวเลือกแผนก)
    assert f'<option value="{d1}" selected>โรงงาน F1 › แผนก D1</option>' in html
    post(c, action="set_scope", id=str(uid), factory_id=str(f1), department_id=str(d1))     # ส่งค่าเดิมกลับ ไม่ขยายขอบเขต
    assert tuple(sql(safety_app, "SELECT factory_id, department_id FROM safety_users WHERE id=?", (uid,))[0]) == (f1, d1)


# ---------- ผู้ดูแลระบบคนสุดท้าย: คำสั่งเดียวแบบ atomic ----------
def test_last_admin_guard_single_statement_blocks_second_demotion(tmp_path):
    from safety.db import connect
    from safety.schema import ensure_schema
    from safety.views.users import safe_disable, safe_set_role
    path = str(tmp_path / "g.db")
    a, b = connect(path), connect(path)
    ensure_schema(a)
    x = a.execute("INSERT INTO safety_users(hub_uid,name,role) VALUES(1,'X','admin')").lastrowid
    y = a.execute("INSERT INTO safety_users(hub_uid,name,role) VALUES(2,'Y','admin')").lastrowid
    a.commit()
    # สองผู้ดูแลลดสิทธิ์กันและกันพร้อมกัน (ต่างการเชื่อมต่อ ต่างอ่านสถานะเดิมมาก่อน)
    assert safe_set_role(a, x, "viewer") is True
    assert safe_set_role(b, y, "viewer") is False
    roles = dict(a.execute("SELECT name, role FROM safety_users").fetchall())
    assert roles == {"X": "viewer", "Y": "admin"}
    assert safe_disable(b, y) is False and a.execute("SELECT active FROM safety_users WHERE id=?", (y,)).fetchone()[0] == 1
    assert safe_set_role(b, y, "admin") is True                                              # ตั้งเป็น admin เท่าเดิมได้
    z = a.execute("INSERT INTO safety_users(hub_uid,name,role) VALUES(3,'Z','admin')").lastrowid; a.commit()
    assert safe_disable(b, y) is True and safe_set_role(a, z, "viewer") is False             # เหลือ Z คนเดียว


def test_inactive_admin_is_not_counted_and_non_admin_changes_unaffected(tmp_path):
    from safety.db import connect
    from safety.schema import ensure_schema
    from safety.views.users import safe_disable, safe_set_role
    db = connect(str(tmp_path / "g.db")); ensure_schema(db)
    x = db.execute("INSERT INTO safety_users(hub_uid,name,role) VALUES(1,'X','admin')").lastrowid
    db.execute("INSERT INTO safety_users(hub_uid,name,role,active) VALUES(2,'Gone','admin',0)")
    r = db.execute("INSERT INTO safety_users(hub_uid,name,role) VALUES(3,'R','reporter')").lastrowid
    db.commit()
    assert safe_set_role(db, x, "viewer") is False and safe_disable(db, x) is False
    assert safe_set_role(db, r, "assessor") is True and safe_disable(db, r) is True
