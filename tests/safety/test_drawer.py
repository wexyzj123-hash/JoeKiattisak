import pytest

from safety.db import connect
from safety import drawer
from tests.safety.factories import add_chain, add_hazard, add_incident, add_user, user_row
from tests.safety.helpers import login


@pytest.fixture
def env(safety_app):
    con = connect(safety_app.config["DATABASE"])
    a, b = add_chain(con, "A"), add_chain(con, "B")
    rep_id = add_user(con, "reporter", "ผู้แจ้ง")
    owner = add_user(con, "assessor", "ผู้รับผิดชอบ")
    h_a = add_hazard(con, a["a"], rep_id, code="HZ-A", status="assigned", owner_id=owner, due_date="2020-01-01", level_name="สูง", level_color="#ff4d4f", score=15,
                     title="เครื่องจักรไม่มีการ์ด", description="รายละเอียด " * 80)
    h_b = add_hazard(con, b["a"], rep_id, code="HZ-B", status="rejected")
    inc = add_incident(con, a["a"], rep_id, code="IN-A", status="investigating")
    con.execute("INSERT INTO incident_people(incident_id, role, name) VALUES(?,?,?)", (inc, "injured", "สมชาย ลับมาก"))
    cl = con.execute("INSERT INTO checklists(code,name_th) VALUES('C','ตรวจ')").lastrowid
    rnd = con.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status) VALUES('PT-A',?,?,?,'draft')", (cl, a["a"], owner)).lastrowid
    con.execute("UPDATE patrol_rounds SET status='submitted', submitted_at='2026-03-01 10:00:00', passed=3, failed=1, score_pct=75 WHERE id=?", (rnd,))
    cp = con.execute("INSERT INTO capas(code, source_kind, action_type, title, risk_area_id, owner_id, due_date, status, created_by) VALUES('CP-A','manual','corrective','ติดตั้งการ์ด',?,?,?,?,?)",
                     (a["a"], owner, "2026-12-01", "done", owner)).lastrowid
    con.execute("INSERT INTO hazard_events(hazard_id, kind, from_status, to_status) VALUES(?,?,?,?)", (h_a, "assigned", "assessed", "assigned"))
    con.commit()
    admin = login(safety_app, "admin")
    mgr_a = login(safety_app, "assessor", factory_id=a["f"])
    mgr_b = login(safety_app, "assessor", factory_id=b["f"])
    return type("E", (), dict(app=safety_app, admin=admin, mgr_a=mgr_a, mgr_b=mgr_b, h_a=h_a, h_b=h_b, inc=inc, rnd=rnd, cp=cp, con=con))


def test_stepper_states_for_each_flow():
    st = drawer.stepper("hazard", "assigned")
    assert [(s["key"], s["state"]) for s in st] == [("reported", "done"), ("assessed", "done"), ("assigned", "current"), ("closed", "todo")]
    assert [s["state"] for s in drawer.stepper("hazard", "closed")] == ["done"] * 4
    rej = drawer.stepper("hazard", "rejected")
    assert [s["state"] for s in rej] == ["done", "bad"] and rej[1]["label"] == "ปฏิเสธ"
    assert [s["key"] for s in drawer.stepper("incident", "actions_set")] == ["reported", "investigating", "actions_set", "closed"]
    assert [s["state"] for s in drawer.stepper("capa", "done")] == ["done", "done", "current", "todo"] and drawer.stepper("capa", "cancelled")[-1]["state"] == "bad"
    assert [s["state"] for s in drawer.stepper("patrol", "submitted")] == ["done", "done"] and [s["state"] for s in drawer.stepper("patrol", "draft")] == ["current", "todo"]
    assert [s["state"] for s in drawer.stepper("capa", "verified")] == ["done"] * 4
    assert drawer.stepper("hazard", "unknown") == []


def test_hazard_drawer_content_and_overdue(env):
    r = env.admin.get(f"/drawer/hazard/{env.h_a}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "HZ-A" in html and "เครื่องจักรไม่มีการ์ด" in html and "มอบหมายแล้ว" in html and "เกินกำหนด" in html
    assert 'class="stepper"' in html and html.count('class="step ') == 4 and f'href="/hazards/{env.h_a}"' in html and "มอบหมาย" in html
    assert len(html) < 8000 and "รายละเอียด " * 60 not in html                              # ตัดคำอธิบายยาวให้สั้น


def test_drawer_scope_idor_and_unknown_kind(env):
    assert env.mgr_a.get(f"/drawer/hazard/{env.h_b}").status_code == 404 and env.mgr_b.get(f"/drawer/hazard/{env.h_a}").status_code == 404
    assert env.admin.get("/drawer/hazard/99999").status_code == 404 and env.admin.get("/drawer/other/1").status_code == 404
    assert env.mgr_a.get(f"/drawer/hazard/{env.h_a}").status_code == 200
    from safety.app import create_app
    assert env.app.test_client().get(f"/drawer/hazard/{env.h_a}").status_code in (302, 401)


def test_incident_drawer_never_shows_people_names(env):
    html = env.admin.get(f"/drawer/incident/{env.inc}").get_data(as_text=True)
    assert "IN-A" in html and "กำลังสอบสวน" in html and "สมชาย" not in html


def test_patrol_and_capa_drawers(env):
    p = env.admin.get(f"/drawer/patrol/{env.rnd}").get_data(as_text=True)
    assert "PT-A" in p and "75%" in p and "ผ่าน 3" in p and "ไม่ผ่าน 1" in p
    c = env.admin.get(f"/drawer/capa/{env.cp}").get_data(as_text=True)
    assert "CP-A" in c and "เสร็จ รอยืนยัน" in c and 'class="stepper"' in c


def test_reporter_cannot_open_others_items(env, safety_app):
    other = login(safety_app, "reporter")
    assert other.get(f"/drawer/hazard/{env.h_a}").status_code == 404 and other.get(f"/drawer/incident/{env.inc}").status_code == 404
