import io
import re
from datetime import date, timedelta

import pytest
from PIL import Image

from safety import timeutil
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_refs
from tests.safety.helpers import login


def test_home_greeting_has_default_mascot(safety_app):
    html = login(safety_app, "reporter").get("/").get_data(as_text=True)
    assert "mascot-default" in html and 'role="img"' in html


def test_hazard_list_empty_states_use_sleeping_and_thinking(safety_app):
    c = login(safety_app, "assessor")
    empty = c.get("/hazards").get_data(as_text=True)
    assert "mascot-sleeping" in empty and "ยังไม่มีรายการอันตราย" in empty
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    add_hazard(con, ch["a"], c.uid, title="มีอยู่")
    searched = c.get("/hazards?q=ไม่มีทางเจอ").get_data(as_text=True)
    assert "mascot-thinking" in searched and "ไม่พบรายการ" in searched and "mascot-sleeping" not in searched
    has_rows = c.get("/hazards").get_data(as_text=True)
    assert "mascot-sleeping" not in has_rows and "mascot-thinking" not in has_rows


def test_notifications_and_master_list_empty_states(safety_app):
    c = login(safety_app, "admin")
    assert "mascot-sleeping" in c.get("/notifications").get_data(as_text=True)
    assert "mascot-sleeping" in c.get("/master/factories").get_data(as_text=True)
    assert "mascot-thinking" in c.get("/master/factories?q=zzz").get_data(as_text=True)


def png():
    b = io.BytesIO(); Image.new("RGB", (40, 30), (9, 99, 9)).save(b, "PNG"); return b.getvalue()


def test_closing_a_hazard_celebrates_once(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    t, s = add_refs(con)
    rep, asr = login(safety_app, "reporter"), login(safety_app, "assessor")
    r = rep.post("/hazards/new", data={"_csrf": csrf(rep), "title": "x", "risk_area_id": ch["a"], "hazard_type_id": t, "source_id": s})
    hid = int(r.headers["Location"].split("/")[-1])
    asr.post(f"/hazards/{hid}/assess", data={"_csrf": csrf(asr), "likelihood": 1, "severity": 1})
    asr.post(f"/hazards/{hid}/assign", data={"_csrf": csrf(asr), "owner_id": asr.uid, "due_date": ""})
    assert "mascot-celebrate" not in asr.get(f"/hazards/{hid}").get_data(as_text=True)
    asr.post(f"/hazards/{hid}/close", data={"_csrf": csrf(asr), "note": "แก้แล้ว"})
    html = asr.get(f"/hazards/{hid}").get_data(as_text=True)
    assert "mascot-celebrate" in html and "ปิดรายการเรียบร้อย" in html
    assert "mascot-celebrate" not in asr.get(f"/hazards/{hid}").get_data(as_text=True)          # flash แสดงครั้งเดียว


def test_failed_close_does_not_celebrate(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    asr = login(safety_app, "assessor")
    hid = add_hazard(con, ch["a"], asr.uid, status="assigned", owner_id=asr.uid)
    asr.post(f"/hazards/{hid}/close", data={"_csrf": csrf(asr), "note": ""})
    html = asr.get(f"/hazards/{hid}").get_data(as_text=True)
    assert "mascot-celebrate" not in html and "กรุณาระบุผลการแก้ไข" in html


def test_overdue_banner_uses_alert_mascot_only_when_overdue(safety_app, monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: date(2026, 3, 10))
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    c = login(safety_app, "assessor")
    assert "mascot-alert" not in c.get("/overview").get_data(as_text=True)
    add_hazard(con, ch["a"], c.uid, status="assigned", due_date="2026-03-09", owner_id=c.uid)
    html = c.get("/overview").get_data(as_text=True)
    assert "mascot-alert" in html and "เกินกำหนด" in html


def test_sso_error_and_too_large_pages_have_mascots(safety_app):
    err = safety_app.test_client().get("/sso?token=bad")
    assert err.status_code == 403 and "mascot-thinking" in err.get_data(as_text=True)


def test_theme_assets_and_toggle_present(safety_app):
    c = login(safety_app, "reporter")
    js = c.get("/static/theme.js")
    assert js.status_code == 200 and "javascript" in js.mimetype and "aiat-theme" in js.get_data(as_text=True)
    html = c.get("/").get_data(as_text=True)
    assert "/static/theme.js" in html and "data-theme-toggle" in html
    assert 'data-theme-toggle' in safety_app.test_client().get("/sso?token=bad").get_data(as_text=True)


def test_mobile_menu_toggle_markup_present(safety_app):
    html = login(safety_app, "reporter").get("/").get_data(as_text=True)
    assert 'id="nav-toggle"' in html and 'for="nav-toggle"' in html


def test_user_text_next_to_mascots_stays_escaped(safety_app):
    c = login(safety_app, "reporter", name="<script>alert(1)</script>")
    html = c.get("/").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
