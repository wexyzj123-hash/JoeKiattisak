"""บัญชีผู้ใช้ของ Safety เอง: ล็อกอิน, ผู้ดูแลคนแรก, ขอสร้างบัญชี → ผู้ดูแลอนุมัติ, ตำแหน่ง/ระดับ, สรุปงานค้าง"""
import re
from datetime import date

import pytest

from safety import accounts, digest
from safety.db import connect
from safety.views import auth as auth_view
from shared.auth import check_password, hash_password
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_user, user_row
from tests.safety.helpers import login, make_user, sql


@pytest.fixture(autouse=True)
def fresh_guard(monkeypatch):
    monkeypatch.setattr(auth_view, "guard", auth_view.LoginGuard())


def post(c, path, **data):
    return c.post(path, data={"_csrf": csrf(c), **data})


def add_native(app, username, password="Passw0rd!", role="reporter", **kw):
    con = connect(app.config["DATABASE"])
    uid = accounts.create_user(con, username=username, name=kw.pop("name", username), password=password, role=role, **kw)
    con.close()
    return uid


def admin_with_password(app):
    add_native(app, "boss", role="admin")


# ---------- ผู้ดูแลคนแรก ----------
def test_setup_creates_first_admin_once_and_logs_in(safety_app):
    c = safety_app.test_client()
    assert c.get("/login").headers["Location"] == "/setup"                                     # ยังไม่มีบัญชีที่ล็อกอินได้เลย
    assert c.get("/setup").status_code == 200
    r = post(c, "/setup", username="root", name="ผู้ดูแล", password="Passw0rd!", password2="Passw0rd!")
    assert r.status_code == 302 and c.get("/").status_code == 200
    row = sql(safety_app, "SELECT role, active, pending, hub_uid, password_hash FROM safety_users WHERE username='root'")[0]
    assert (row["role"], row["active"], row["pending"]) == ("admin", 1, 0) and row["hub_uid"] < 0 and row["password_hash"] != "Passw0rd!"
    assert safety_app.test_client().get("/setup").headers["Location"] == "/login"             # ใช้ได้ครั้งเดียว


def test_setup_validates_and_never_overrides_existing_admin(safety_app):
    c = safety_app.test_client()
    for bad in ({"username": "a b"}, {"password": "short", "password2": "short"}, {"password2": "different1"}, {"name": " "}):
        data = {"username": "root", "name": "ผู้ดูแล", "password": "Passw0rd!", "password2": "Passw0rd!", **bad}
        assert post(c, "/setup", **data).status_code == 200
    assert sql(safety_app, "SELECT COUNT(*) FROM safety_users")[0][0] == 0
    admin_with_password(safety_app)
    assert post(safety_app.test_client(), "/setup", username="evil", name="x", password="Passw0rd!", password2="Passw0rd!").status_code == 302
    assert sql(safety_app, "SELECT COUNT(*) FROM safety_users WHERE username='evil'")[0][0] == 0


# ---------- ล็อกอิน ----------
def test_login_success_wrong_password_unknown_user_and_safe_next(safety_app):
    admin_with_password(safety_app)
    c = safety_app.test_client()
    page = c.get("/login").get_data(as_text=True)
    assert 'class="ss"' in page and "AIAT Safety" in page and "mascot" not in page and "Environment" not in page and "เข้าสู่ระบบครั้งเดียว" not in page
    assert "/register" in page and "กลับหน้า Hub" in page
    bad = post(c, "/login", username="boss", password="wrong")
    assert bad.status_code == 200 and "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง" in bad.get_data(as_text=True)
    ghost = post(c, "/login", username="nobody", password="Passw0rd!")
    assert "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง" in ghost.get_data(as_text=True)                      # ข้อความเดียวกัน ไม่บอกว่าชื่อมีอยู่หรือไม่
    with c.session_transaction() as s:
        assert "suid" not in s
    ok = post(c, "/login", username="BOSS", password="Passw0rd!", next="/hazards")               # ชื่อผู้ใช้ไม่สนตัวพิมพ์
    assert ok.status_code == 302 and ok.headers["Location"] == "/hazards"
    out = post(safety_app.test_client(), "/login", username="boss", password="Passw0rd!", next="//evil.example/x")
    assert out.headers["Location"] == "/"
    out = post(safety_app.test_client(), "/login", username="boss", password="Passw0rd!", next="https://evil.example")
    assert out.headers["Location"] == "/"


def test_anonymous_gets_login_with_next_and_returns_after_login(safety_app):
    admin_with_password(safety_app)
    c = safety_app.test_client()
    r = c.get("/hazards?status=reported")
    assert r.headers["Location"].startswith("/login?next=") and "hazards" in r.headers["Location"]
    nxt = re.search(r"next=([^&]+)", r.headers["Location"]).group(1)
    from urllib.parse import unquote
    done = post(c, "/login", username="boss", password="Passw0rd!", next=unquote(nxt))
    assert done.headers["Location"].startswith("/hazards")


def test_login_blocks_pending_inactive_and_accounts_without_a_password(safety_app):
    admin_with_password(safety_app)
    add_native(safety_app, "wait", active=0, pending=1)
    add_native(safety_app, "off", active=0)
    legacy = make_user(safety_app, "reporter", name="เดิมจาก SSO")                                # ไม่มีรหัสผ่าน
    c = safety_app.test_client()
    assert "รอผู้ดูแลระบบอนุมัติ" in post(c, "/login", username="wait", password="Passw0rd!").get_data(as_text=True)
    assert "ถูกปิดใช้งาน" in post(c, "/login", username="off", password="Passw0rd!").get_data(as_text=True)
    assert "ไม่ถูกต้อง" in post(c, "/login", username="", password="").get_data(as_text=True)
    with c.session_transaction() as s:
        assert "suid" not in s
    assert not check_password(sql(safety_app, "SELECT password_hash FROM safety_users WHERE id=?", (legacy,))[0][0], "")


def test_login_is_locked_after_repeated_failures_and_a_correct_password_does_not_bypass(safety_app):
    admin_with_password(safety_app)
    c = safety_app.test_client()
    for _ in range(5):
        post(c, "/login", username="boss", password="nope")
    r = post(c, "/login", username="boss", password="Passw0rd!")
    assert r.status_code == 429 and "ลองผิดหลายครั้ง" in r.get_data(as_text=True)
    other = safety_app.test_client()
    assert post(other, "/login", username="someone-else", password="x").status_code == 200       # ชื่ออื่นจาก IP เดียวกันยังไม่ถูกล็อก (จนกว่าจะเกินเพดาน IP)


def test_guard_unit_window_and_unlock():
    g = auth_view.LoginGuard()
    for i in range(5):
        g.failed("1.1.1.1", "u", now=100 + i)
    assert g.locked_for("1.1.1.1", "u", now=110) > 0 and g.locked_for("1.1.1.1", "other", now=110) == 0
    assert g.locked_for("1.1.1.1", "u", now=100 + 4 + 301) == 0                                  # ครบเวลาล็อกแล้วเข้าได้
    g.failed("2.2.2.2", "u", now=0); g.ok("2.2.2.2", "u")
    for i in range(4):
        g.failed("2.2.2.2", "u", now=10 + i)
    assert g.locked_for("2.2.2.2", "u", now=20) == 0                                              # ล็อกอินสำเร็จแล้วล้างตัวนับ


def test_native_users_are_not_checked_against_the_hub_and_hub_login_is_gone_from_session(safety_app, monkeypatch):
    admin_with_password(safety_app)
    safety_app.config["HUB_STATUS_CHECK"] = True
    from safety import hubcheck
    monkeypatch.setattr(hubcheck, "hub_user_active", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call the hub")))
    c = safety_app.test_client()
    post(c, "/login", username="boss", password="Passw0rd!")
    assert c.get("/").status_code == 200


# ---------- ขอสร้างบัญชี ----------
@pytest.fixture
def org(safety_app):
    con = connect(safety_app.config["DATABASE"])
    a, b = add_chain(con, "A"), add_chain(con, "B")
    con.close()
    admin_with_password(safety_app)
    pos = sql(safety_app, "SELECT id FROM positions WHERE code='SV'")[0][0]
    lv = sql(safety_app, "SELECT id FROM user_levels WHERE code='L1'")[0][0]
    return {"a": a, "b": b, "pos": pos, "lv": lv}


def form(org, **kw):
    return {"username": "newbie", "name": "สมหญิง ใจดี", "factory_id": str(org["a"]["f"]), "department_id": str(org["a"]["d"]), "line_id": str(org["a"]["l"]),
            "position_id": str(org["pos"]), "level_id": str(org["lv"]), "email": "sy@example.com", "password": "Passw0rd!", "password2": "Passw0rd!", **kw}


def test_seeded_positions_and_levels(safety_app):
    pos = [r[0] for r in sql(safety_app, "SELECT name_th FROM positions ORDER BY id")]
    assert pos == ["Operator", "LL", "Staff", "Sr.Staff", "FM", "SV", "SSV", "ผู้จัดการ (Manager)", "DM", "DGM", "GM"]
    assert [r[0] for r in sql(safety_app, "SELECT name_th FROM user_levels ORDER BY code")] == ["L0", "L1", "L2"]


def test_register_creates_a_pending_inactive_reporter_with_org_position_level(safety_app, org):
    c = safety_app.test_client()
    page = c.get("/register").get_data(as_text=True)
    assert "โรงงาน A" in page and "Sr.Staff" in page and "L2" in page and 'id="f-dept"' in page and "/static/safety/register.js" in page
    r = post(c, "/register", **form(org))
    assert r.status_code == 200 and "ส่งคำขอแล้ว" in r.get_data(as_text=True)
    u = sql(safety_app, "SELECT * FROM safety_users WHERE username='newbie'")[0]
    assert (u["active"], u["pending"], u["role"], u["position_id"], u["level_id"]) == (0, 1, "reporter", org["pos"], org["lv"])
    assert u["requested_org"] == "โรงงาน A › แผนก A › ไลน์ A" and (u["req_factory_id"], u["req_department_id"]) == (org["a"]["f"], org["a"]["d"])
    assert u["email"] == "sy@example.com" and u["hub_uid"] < 0 and check_password(u["password_hash"], "Passw0rd!")
    assert "รอผู้ดูแลระบบอนุมัติ" in post(c, "/login", username="newbie", password="Passw0rd!").get_data(as_text=True)


def test_register_validation_rejects_each_bad_input_and_creates_nothing(safety_app, org):
    c = safety_app.test_client()
    bad = ({"username": "a b"}, {"username": "ab"}, {"username": "boss"}, {"username": "BOSS"}, {"name": " "}, {"password": "short", "password2": "short"},
           {"password2": "other-pass1"}, {"email": "no-at"}, {"factory_id": ""}, {"department_id": ""},
           {"department_id": str(org["b"]["d"])}, {"line_id": str(org["b"]["l"])}, {"position_id": ""}, {"position_id": "9999"},
           {"level_id": ""}, {"level_id": "9999"}, {"factory_id": "x"})
    for extra in bad:
        r = post(c, "/register", **form(org, **extra))
        assert r.status_code == 200 and "ส่งคำขอแล้ว" not in r.get_data(as_text=True), extra
    assert sql(safety_app, "SELECT COUNT(*) FROM safety_users WHERE username IN ('newbie','a b','ab')")[0][0] == 0
    ok = post(c, "/register", **form(org, line_id=""))                                              # ไลน์ไม่บังคับ
    assert "ส่งคำขอแล้ว" in ok.get_data(as_text=True)
    assert sql(safety_app, "SELECT requested_org FROM safety_users WHERE username='newbie'")[0][0] == "โรงงาน A › แผนก A"


def test_register_hides_closed_and_inactive_org_and_respects_switch_and_cap(safety_app, org, monkeypatch):
    sql(safety_app, "UPDATE departments SET active=0 WHERE id=?", (org["b"]["d"],))
    c = safety_app.test_client()
    assert "แผนก B" not in c.get("/register").get_data(as_text=True).split('id="org-data"')[1].split("</script>")[0]
    monkeypatch.setattr(accounts, "MAX_PENDING", 1)
    post(c, "/register", **form(org))
    assert "มากเกินไป" in post(c, "/register", **form(org, username="second")).get_data(as_text=True)
    monkeypatch.setattr(accounts, "MAX_PENDING", 50)
    sql(safety_app, "UPDATE app_settings SET value='0' WHERE key='self_register'")
    assert c.get("/register").status_code == 403 and "ปิดรับคำขอ" in c.get("/register").get_data(as_text=True)
    assert post(c, "/register", **form(org, username="third")).status_code == 403
    assert 'href="/register"' not in c.get("/login").get_data(as_text=True)


def test_register_notifies_admins_only_and_leaks_no_password(safety_app, org):
    nothing = add_native(safety_app, "plain")                                                          # ผู้ใช้ทั่วไปไม่ได้รับ
    post(safety_app.test_client(), "/register", **form(org))
    rows = sql(safety_app, "SELECT n.title, n.body, n.link, u.username FROM notifications n JOIN safety_users u ON u.id=n.user_id WHERE n.event='user_requested'")
    assert [r["username"] for r in rows] == ["boss"] and rows[0]["link"] == "/admin/users#pending"
    blob = " ".join(map(str, tuple(rows[0])))
    assert "Passw0rd" not in blob and "sy@example.com" not in blob and "สมหญิง" in blob and "โรงงาน A" in blob and "SV" in blob


# ---------- ผู้ดูแลจัดการผู้ใช้ ----------
def request_user(app, org):
    post(app.test_client(), "/register", **form(org))
    return sql(app, "SELECT id FROM safety_users WHERE username='newbie'")[0][0]


def admin_client(app):
    c = app.test_client()
    post(c, "/login", username="boss", password="Passw0rd!")
    return c


def test_admin_sees_pending_box_with_requested_org_and_prefilled_scope(safety_app, org):
    uid = request_user(safety_app, org)
    html = admin_client(safety_app).get("/admin/users").get_data(as_text=True)
    assert "รออนุมัติ (1)" in html and "โรงงาน A › แผนก A › ไลน์ A" in html and "SV" in html
    assert re.search(r'<option value="%d" selected>โรงงาน A</option>' % org["a"]["f"], html)


def test_approve_activates_with_chosen_role_scope_and_the_user_can_login(safety_app, org):
    uid = request_user(safety_app, org)
    c = admin_client(safety_app)
    post(c, "/admin/users", action="approve", id=uid, role="assessor", factory_id=org["a"]["f"], department_id=org["a"]["d"], position_id=org["pos"], level_id=org["lv"])
    u = sql(safety_app, "SELECT * FROM safety_users WHERE id=?", (uid,))[0]
    assert (u["active"], u["pending"], u["role"], u["factory_id"], u["department_id"]) == (1, 0, "assessor", org["a"]["f"], org["a"]["d"])
    assert post(safety_app.test_client(), "/login", username="newbie", password="Passw0rd!").headers["Location"] == "/"
    assert "รออนุมัติ" not in c.get("/admin/users").get_data(as_text=True).split("<h2>เพิ่มผู้ใช้")[0]


def test_approve_cannot_grant_more_than_the_approver_has_and_pending_cannot_be_toggled(safety_app, org):
    uid = request_user(safety_app, org)
    sql(safety_app, "INSERT INTO safety_roles(key,name_th,permissions,is_system) VALUES('hr','ฝ่ายบุคคล','users.manage,master.view',0)")
    hr = add_native(safety_app, "hr1", role="hr")
    c = safety_app.test_client()
    post(c, "/login", username="hr1", password="Passw0rd!")
    post(c, "/admin/users", action="approve", id=uid, role="admin")
    assert sql(safety_app, "SELECT pending, role FROM safety_users WHERE id=?", (uid,))[0][:] == (1, "reporter")
    post(c, "/admin/users", action="toggle", id=uid)
    post(c, "/admin/users", action="set_role", id=uid, role="assessor")
    assert sql(safety_app, "SELECT pending, active, role FROM safety_users WHERE id=?", (uid,))[0][:] == (1, 0, "reporter")
    post(c, "/admin/users", action="approve", id=uid, role="viewer")
    assert sql(safety_app, "SELECT pending, active, role FROM safety_users WHERE id=?", (uid,))[0][:] == (0, 1, "viewer")


def test_reject_deletes_only_pending_and_registration_switch_works(safety_app, org):
    uid = request_user(safety_app, org)
    c = admin_client(safety_app)
    boss = sql(safety_app, "SELECT id FROM safety_users WHERE username='boss'")[0][0]
    post(c, "/admin/users", action="reject", id=boss)
    assert sql(safety_app, "SELECT COUNT(*) FROM safety_users WHERE id=?", (boss,))[0][0] == 1
    post(c, "/admin/users", action="reject", id=uid)
    assert sql(safety_app, "SELECT COUNT(*) FROM safety_users WHERE id=?", (uid,))[0][0] == 0
    post(c, "/admin/users", action="set_registration", open="0")
    assert safety_app.test_client().get("/register").status_code == 403
    post(c, "/admin/users", action="set_registration", open="1")
    assert safety_app.test_client().get("/register").status_code == 200
    plain = safety_app.test_client()
    add_native(safety_app, "viewer1", role="viewer")
    post(plain, "/login", username="viewer1", password="Passw0rd!")
    assert post(plain, "/admin/users", action="set_registration", open="0").status_code == 403


def test_admin_creates_user_with_position_level_and_reset_password(safety_app, org):
    c = admin_client(safety_app)
    post(c, "/admin/users", action="create", username="somchai", name="สมชาย", password="Initial-pass1", role="reporter", position_id=org["pos"], level_id=org["lv"],
         factory_id=org["a"]["f"], department_id=org["a"]["d"], email="sc@example.com")
    u = sql(safety_app, "SELECT * FROM safety_users WHERE username='somchai'")[0]
    assert (u["active"], u["pending"], u["role"], u["position_id"], u["level_id"], u["factory_id"]) == (1, 0, "reporter", org["pos"], org["lv"], org["a"]["f"])
    assert post(safety_app.test_client(), "/login", username="somchai", password="Initial-pass1").status_code == 302
    for bad in ({"username": "somchai"}, {"username": "x y"}, {"password": "short"}, {"role": "ghost"}):
        before = sql(safety_app, "SELECT COUNT(*) FROM safety_users")[0][0]
        post(c, "/admin/users", action="create", **{"username": "u2", "name": "n", "password": "Initial-pass1", "role": "reporter", **bad})
        assert sql(safety_app, "SELECT COUNT(*) FROM safety_users")[0][0] == before, bad
    post(c, "/admin/users", action="reset_password", id=u["id"], password="Brand-new-pass2")
    assert post(safety_app.test_client(), "/login", username="somchai", password="Initial-pass1").status_code == 200
    assert post(safety_app.test_client(), "/login", username="somchai", password="Brand-new-pass2").status_code == 302
    post(c, "/admin/users", action="reset_password", id=u["id"], password="short")
    assert check_password(sql(safety_app, "SELECT password_hash FROM safety_users WHERE id=?", (u["id"],))[0][0], "Brand-new-pass2")
    post(c, "/admin/users", action="set_profile", id=u["id"], position_id="", level_id="")
    assert sql(safety_app, "SELECT position_id, level_id FROM safety_users WHERE id=?", (u["id"],))[0][:] == (None, None)
    post(c, "/admin/users", action="set_profile", id=u["id"], position_id="9999", level_id="")
    assert sql(safety_app, "SELECT position_id FROM safety_users WHERE id=?", (u["id"],))[0][0] is None


def test_users_page_escapes_requested_org_and_names(safety_app, org):
    uid = add_native(safety_app, "xss1", name="<b>x</b>", active=0, pending=1, requested_org="<script>alert(1)</script>")
    html = admin_client(safety_app).get("/admin/users").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "<b>x</b>" not in html and "&lt;script&gt;" in html


def test_manager_position_and_custom_position_can_be_added(safety_app):
    c = login(safety_app, "admin")
    r = c.post("/master/positions/new", data={"_csrf": csrf(c), "code": "LEAD", "name_th": "หัวหน้าทีม", "description": ""})
    assert r.status_code == 302 and sql(safety_app, "SELECT name_th FROM positions WHERE code='LEAD'")[0][0] == "หัวหน้าทีม"
    assert "หัวหน้าทีม" in c.get("/master/positions").get_data(as_text=True) and "ผู้จัดการ (Manager)" in c.get("/master/positions").get_data(as_text=True)


# ---------- สรุปงานค้างรายวัน ----------
def test_digest_counts_what_each_person_must_do_and_sends_once_per_day(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "D")
    rep = add_user(con, "reporter", "ผู้แจ้ง")
    owner = add_user(con, "assessor", "เจ้าของ")
    con.execute("UPDATE safety_users SET email='o@example.com', notify_channels='inapp,email' WHERE id=?", (owner,))
    con.execute("UPDATE safety_users SET email='r@example.com', notify_channels='inapp,email' WHERE id=?", (rep,))
    con.commit()
    add_hazard(con, ch["a"], rep, status="assigned", owner_id=owner, due_date="2026-03-01")
    add_hazard(con, ch["a"], rep, status="assigned", owner_id=owner, due_date="2026-04-30")
    add_hazard(con, ch["a"], rep, status="reported")
    today = date(2026, 3, 10)
    items = {label: c for label, c, _ in digest.backlog(con, user_row(con, owner), today)}
    assert items["รายการอันตรายที่ได้รับมอบหมายและเกินกำหนด"] == 1 and items["รายการอันตรายที่ได้รับมอบหมาย (ยังไม่เกินกำหนด)"] == 1
    assert items["รายการอันตรายที่รอประเมินความเสี่ยง"] == 1                                               # จป. เห็นรายการรอประเมินด้วย
    assert digest.backlog(con, user_row(con, rep), today) == []                                            # ผู้แจ้งไม่มีงานค้างของตัวเอง
    assert digest.queue_all(con, today) == 1
    row = con.execute("SELECT channel, target, subject, body FROM notify_outbox").fetchone()
    assert (row["channel"], row["target"]) == ("email", "o@example.com") and "3 รายการ" in row["subject"] and "เกินกำหนด 1" in row["body"]
    assert digest.queue_all(con, today) == 0                                                               # วันเดียวกันไม่ส่งซ้ำ
    assert digest.queue_all(con, date(2026, 3, 11)) == 1
    con.execute("UPDATE app_settings SET value='0' WHERE key='backlog_digest'")
    assert digest.queue_all(con, date(2026, 3, 12)) == 0                                                   # ปิดสวิตช์ = ไม่ส่ง
    con.close()


def test_digest_skips_users_without_opt_in_or_valid_contact_and_runs_in_the_daily_job(safety_app, tmp_path, monkeypatch):
    from safety import scheduler
    monkeypatch.setattr("safety.scheduler.backup_database", lambda *a, **k: str(tmp_path / "b.db"))
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "E")
    rep = add_user(con, "reporter", "ผู้แจ้ง")
    a = add_user(con, "assessor", "ไม่เลือกรับ")
    b = add_user(con, "assessor", "อีเมลเสีย")
    con.execute("UPDATE safety_users SET email='ok@example.com' WHERE id=?", (a,))
    con.execute("UPDATE safety_users SET email='nope', notify_channels='inapp,email' WHERE id=?", (b,))
    con.commit()
    for o in (a, b):
        add_hazard(con, ch["a"], rep, status="assigned", owner_id=o, due_date="2026-01-01")
    res = scheduler.run_daily(con, today=date(2026, 3, 10), force=True)
    assert res["digest"] == 0 and con.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] == 0
    con.close()
