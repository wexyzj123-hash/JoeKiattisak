"""ฟอร์มแจ้งอันตรายแบบเต็ม (ตามทะเบียนบันทึกอันตรายของระบบเดิม) และรายการตัวเลือกที่ผู้ดูแลแก้ได้เองบนเว็บ"""
from datetime import date

import pytest

from safety import timeutil
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_refs
from tests.safety.helpers import login

TODAY = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)


@pytest.fixture
def env(safety_app):
    db = connect(safety_app.config["DATABASE"])
    chain = add_chain(db, "A")
    t, s = add_refs(db)
    return safety_app, db, chain, t, s


def _form(env, **kw):
    app, db, chain, t, s = env
    return {"title": "น้ำมันหกพื้น", "risk_area_id": str(chain["a"]), "hazard_type_id": str(t), "source_id": str(s), **kw}


def _post(env, **kw):
    c = login(env[0], "reporter")
    return c, c.post("/hazards/new", data={"_csrf": csrf(c), **_form(env, **kw)})


def test_lists_are_seeded_and_manageable_from_master_pages(env):
    app, db, *_ = env
    assert db.execute("SELECT COUNT(*) FROM hiyari_types").fetchone()[0] >= 3
    assert db.execute("SELECT COUNT(*) FROM safety_measures").fetchone()[0] >= 3
    c = login(app, "admin")
    for slug in ("hiyari-types", "safety-measures"):
        assert c.get(f"/master/{slug}").status_code == 200
    r = c.post("/master/safety-measures/new", data={"_csrf": csrf(c), "code": "ZZ", "name_th": "มาตรการใหม่ของโรงงาน"})
    assert r.status_code in (302, 200)
    assert db.execute("SELECT 1 FROM safety_measures WHERE code='ZZ'").fetchone()
    page = login(app, "reporter").get("/hazards/new").get_data(as_text=True)
    assert "มาตรการใหม่ของโรงงาน" in page and "เลขที่เอกสาร" in page and 'name="rpt_severity"' in page


def test_full_report_stores_every_field_and_score(env):
    app, db, *_ = env
    h = db.execute("SELECT id FROM hiyari_types ORDER BY id LIMIT 1").fetchone()[0]
    m = db.execute("SELECT id FROM safety_measures ORDER BY id LIMIT 1").fetchone()[0]
    c, r = _post(env, occurred_on="2026-03-09", observer="สมชาย", location_text="หน้าเครื่อง", machine_no="MC-12",
                 hiyari_type_id=str(h), measure_id=str(m), temp_action="กั้นเขต", perm_action="ติดการ์ด",
                 plan_due_date="2026-04-01", rpt_severity="2", rpt_likelihood="3")
    assert r.status_code == 302
    row = db.execute("SELECT * FROM hazards ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["occurred_on"], row["observer"], row["machine_no"], row["plan_due_date"]) == ("2026-03-09", "สมชาย", "MC-12", "2026-04-01")
    assert (row["rpt_severity"], row["rpt_likelihood"], row["rpt_score"]) == (2, 3, 6)
    assert row["status"] == "reported" and row["score"] is None            # คะแนนทางการยังรอผู้ประเมิน
    page = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "MC-12" in page and "ติดการ์ด" in page and "2026-04-01" in page


def test_extra_fields_are_optional_and_validated(env):
    app, db, *_ = env
    assert _post(env)[1].status_code == 302
    for bad in ({"occurred_on": "2026-03-11"}, {"occurred_on": "x"}, {"plan_due_date": "31/12"}, {"hiyari_type_id": "9999"},
                {"rpt_severity": "2"}, {"rpt_severity": "99", "rpt_likelihood": "1"}, {"machine_no": "x" * 201}):
        r = _post(env, **bad)[1]
        assert r.status_code == 200, bad
    db.execute("UPDATE safety_measures SET active=0")
    db.commit()
    assert _post(env, measure_id="1")[1].status_code == 200            # รายการที่ปิดใช้งานเลือกไม่ได้
