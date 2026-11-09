"""สิ่งที่ Safety ให้การขอสร้างบัญชีที่ Hub: รายชื่อโรงงาน/แผนก/ไลน์, สังกัดที่ผู้ขอระบุ, และการออกจากระบบแบบเบื้องหลัง"""
from safety.db import connect
from shared.sso import sign_token
from tests.conftest import SECRET
from tests.safety.factories import add_chain
from tests.safety.helpers import login

H = {"X-Service-Token": "svc-token"}


def test_org_api_requires_token_and_lists_only_active_names(safety_app):
    con = connect(safety_app.config["DATABASE"])
    a, b = add_chain(con, "A"), add_chain(con, "B", active=0)
    con.execute("UPDATE departments SET active=0 WHERE id=?", (b["d"],)); con.commit()
    c = safety_app.test_client()
    assert c.get("/api/org").status_code == 401 and c.get("/api/org", headers={"X-Service-Token": "x"}).status_code == 401
    r = c.get("/api/org", headers=H)
    d = r.get_json()
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    assert [f["name"] for f in d["factories"]] == ["โรงงาน A"]
    assert d["departments"] == [{"id": a["d"], "name": "แผนก A", "factory_id": a["f"]}]
    assert d["lines"] == [{"id": a["l"], "name": "ไลน์ A", "department_id": a["d"]}]
    assert set(d) == {"factories", "departments", "lines"}                                            # ไม่มีข้อมูลบุคคล


def tok(**extra):
    return sign_token(SECRET, {"uid": 31, "name": "สมหญิง ใจดี", "roles": ["viewer"], "aud": "safety", **extra})


def test_sso_stores_requested_org_once_and_admin_sees_it(safety_app):
    c = safety_app.test_client()
    c.get("/sso?token=" + tok(org="โรงงานบางนา|ฝ่ายผลิต|ไลน์ประกอบ 1"))
    con = connect(safety_app.config["DATABASE"])
    assert con.execute("SELECT requested_org FROM safety_users WHERE hub_uid=31").fetchone()[0] == "โรงงานบางนา › ฝ่ายผลิต › ไลน์ประกอบ 1"
    safety_app.test_client().get("/sso?token=" + tok(org="อื่น|อื่น"))                                  # ไม่ทับค่าเดิม
    assert con.execute("SELECT requested_org FROM safety_users WHERE hub_uid=31").fetchone()[0].startswith("โรงงานบางนา")
    html = login(safety_app, "admin").get("/admin/users").get_data(as_text=True)
    assert "ระบุเองว่า: โรงงานบางนา › ฝ่ายผลิต › ไลน์ประกอบ 1" in html


def test_sso_without_org_and_with_hostile_org_is_safe(safety_app):
    c = safety_app.test_client()
    c.get("/sso?token=" + tok(org="<script>x</script>|" + "ก" * 500))
    con = connect(safety_app.config["DATABASE"])
    v = con.execute("SELECT requested_org FROM safety_users WHERE hub_uid=31").fetchone()[0]
    assert len(v) <= 300
    assert "&lt;script&gt;" in login(safety_app, "admin").get("/admin/users").get_data(as_text=True) and "<script>x" not in login(safety_app, "admin").get("/admin/users").get_data(as_text=True)


def test_signout_quiet_clears_session_without_redirect(safety_app):
    c = login(safety_app, "admin")
    r = c.get("/signout?quiet=1")
    assert r.status_code == 204 and "no-store" in r.headers["Cache-Control"]
    with c.session_transaction() as s:
        assert "suid" not in s
    c2 = login(safety_app, "admin")
    r = c2.get("/signout")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
