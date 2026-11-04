"""หน้าต่างเด้ง «สรุปงานค้าง»: ค้างอะไร ที่ไหน ขั้นตอนไหน (หรือไม่มีค้าง) กดเข้าได้ทุกรายการ เด้งเองครั้งเดียวต่อการเข้าสู่ระบบ"""
import pytest

from safety import pending
from tests.safety.factories import add_user, user_row
from tests.safety.helpers import login_as
from tests.safety.test_approvals import World


@pytest.fixture
def w(safety_app):
    return World(safety_app)


def test_home_opens_dialog_automatically_once_per_login_and_button_is_on_every_page(w):
    c = login_as(w.app, w.mate)
    first = c.get("/").get_data(as_text=True)
    assert 'id="pending-dialog"' in first and 'data-auto="1"' in first and 'id="pending-open"' in first
    second = c.get("/").get_data(as_text=True)
    assert 'data-auto="1"' not in second and 'id="pending-open"' in second                 # เด้งครั้งเดียว แต่ปุ่มเปิดซ้ำยังอยู่
    assert 'id="pending-open"' in c.get("/unit").get_data(as_text=True)
    assert c.get("/static/safety/pending.js").status_code == 200 and c.get("/static/safety/pending.css").status_code == 200


def test_empty_state_says_nothing_pending(w):
    html = login_as(w.app, w.mate).get("/pending").get_data(as_text=True)
    assert "ไม่มีงานค้างที่รอคุณ" in html and "/unit" in html


def test_mine_section_shows_what_where_and_which_step_with_links(w):
    w.on()
    hid = w.hazard()
    asr = add_user(w.db, "assessor", "ผู้ประเมิน", factory_id=w.a["f"], department_id=w.a["d"])
    html = login_as(w.app, asr).get("/pending").get_data(as_text=True)
    assert "รอคุณประเมินความเสี่ยง" in html and f'href="/hazards/{hid}"' in html and "น้ำมันหก" in html
    assert "พื้นที่ A" in html and "แจ้งแล้ว" in html and 'class="pd-now"' in html            # ที่ไหน + ขั้นตอนปัจจุบัน
    assert "อนุมัติ: ขั้น 1 รออนุมัติ › ขั้น 2 รออนุมัติ" in html
    sv = login_as(w.app, w.sv).get("/pending").get_data(as_text=True)
    assert "รออนุมัติขั้นที่ 1" in sv and 'href="/approvals"' in sv and "คุณเป็นผู้อนุมัติขั้นนี้" in sv


def test_unit_members_see_open_items_of_their_unit_but_not_other_units(w):
    hid, other = w.hazard(), w.hazard(w.b)
    html = login_as(w.app, w.mate).get("/pending").get_data(as_text=True)
    assert "ค้างในหน่วยงานของฉัน" in html and f'href="/hazards/{hid}"' in html and f'href="/hazards/{other}"' not in html
    assert "ขั้นตอน" in html


def test_overdue_and_owner_items_are_flagged_and_sorted_first(w):
    asr = add_user(w.db, "assessor", "เจ้าของงาน", factory_id=w.a["f"], department_id=w.a["d"])
    a, b = w.hazard(), w.hazard()
    for hid, due in ((a, "2026-01-01"), (b, "2099-01-01")):
        w.db.execute("UPDATE hazards SET status='assigned', owner_id=?, due_date=?, likelihood=1, severity=1, score=25, level_name='A1', level_color='#f00' WHERE id=?", (asr, due, hid))
    w.db.commit()
    d = pending.collect(w.db, user_row(w.db, asr))
    assert d["mine_total"] == 2 and d["mine"][0]["overdue"] > 0 and d["mine"][0]["code"] == w.db.execute("SELECT code FROM hazards WHERE id=?", (a,)).fetchone()[0]
    assert "รอคุณดำเนินการแก้ไขและปิดรายการ" in d["mine"][0]["action"] and d["overdue_total"] == 1
    html = login_as(w.app, asr).get("/pending").get_data(as_text=True)
    assert "เกินกำหนด" in html and "A1" in html


def test_user_without_unit_only_sees_items_that_involve_them_and_requires_login(w):
    w.hazard()
    nobody = w._user("viewer", "ไม่ผูกหน่วยงาน", None, None, False)
    d = pending.collect(w.db, user_row(w.db, nobody))
    assert d["others"] == [] and d["mine"] == [] and d["empty"]
    assert w.app.test_client().get("/pending").status_code == 302


def test_incident_and_capa_steps_are_listed(w):
    from safety import incidents, timeutil
    asr = add_user(w.db, "assessor", "ผู้สอบสวน", factory_id=w.a["f"], department_id=w.a["d"])
    iid, errors = incidents.report(w.db, user_row(w.db, w.rep), {
        "kind": "accident", "occurred_at": timeutil.now().strftime("%Y-%m-%dT%H:%M"), "risk_area_id": str(w.a["a"]), "title": "รถตักชนเสา",
        "injury_severity_id": str(w.db.execute("SELECT id FROM injury_severities LIMIT 1").fetchone()[0])})
    assert errors == []
    w.db.execute("INSERT INTO capas(code, source_kind, source_id, action_type, title, risk_area_id, owner_id, due_date, created_by) VALUES('CP-1','manual',NULL,'corrective','ติดตั้งการ์ด',?,?,?,?)",
                 (w.a["a"], asr, "2026-12-31", asr)); w.db.commit()
    html = login_as(w.app, asr).get("/pending").get_data(as_text=True)
    assert "รอคุณมอบหมายผู้สอบสวน" in html and f'href="/incidents/{iid}"' in html and "รอคุณดำเนินการ CAPA" in html and "สอบสวน" in html


def test_item_waiting_on_my_approval_is_not_repeated_in_unit_section(w):
    w.on()
    w.hazard()
    html = login_as(w.app, w.sv).get("/pending").get_data(as_text=True)
    assert html.count("HZ-") == 1 and "ค้างในหน่วยงานของฉัน" not in html
