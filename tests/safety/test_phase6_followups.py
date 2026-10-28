import os
import re
import time
from datetime import datetime

import pytest

from safety import incident_flow as flow
from safety import incident_stats, incidents, timeutil
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_user, severity_id, user_row
from tests.safety.helpers import login

NOW = datetime(2026, 3, 10, 14, 30)


@pytest.fixture
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
    monkeypatch.setattr(timeutil, "today", lambda: NOW.date())


@pytest.fixture
def restore_tz():
    old = os.environ.get("TZ")
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


# ---------- โซนเวลา ----------
def test_apply_timezone_sets_process_clock_to_bangkok_by_default(restore_tz):
    os.environ["TZ"] = "UTC"; time.tzset()
    assert timeutil.apply_timezone() == "Asia/Bangkok"
    assert time.localtime().tm_gmtoff == 7 * 3600
    assert datetime.now().astimezone().utcoffset().total_seconds() == 7 * 3600


def test_apply_timezone_env_override_and_invalid_name_falls_back(restore_tz, monkeypatch):
    monkeypatch.setenv("SAFETY_TIMEZONE", "Asia/Tokyo")
    assert timeutil.apply_timezone() == "Asia/Tokyo" and time.localtime().tm_gmtoff == 9 * 3600
    monkeypatch.setenv("SAFETY_TIMEZONE", "Not/AZone")
    assert timeutil.apply_timezone() == "Asia/Bangkok" and time.localtime().tm_gmtoff == 7 * 3600


def test_create_app_applies_the_timezone_for_both_apps(restore_tz, tmp_path):
    from hub.app import create_app as hub_app
    from safety.app import create_app as safety_app
    for make, name in ((safety_app, "s"), (hub_app, "h")):
        os.environ["TZ"] = "UTC"; time.tzset()
        make({"TESTING": True, "DATABASE": str(tmp_path / f"{name}.db"), "SECRET_KEY": "k", "SSO_SECRET": "t" * 40, "SERVICE_TOKEN": "x",
              "HUB_URL": "http://h", "SAFETY_URL": "http://s", "UPLOAD_ROOT": str(tmp_path / "u")})
        assert time.localtime().tm_gmtoff == 7 * 3600, name


def test_morning_in_thailand_is_not_rejected_as_future_on_a_utc_host(restore_tz, tmp_path):
    os.environ["TZ"] = "UTC"; time.tzset()
    timeutil.apply_timezone()                                                                          # 05:00 UTC = 12:00 ในไทย
    from safety.schema import ensure_schema
    con = connect(str(tmp_path / "s.db")); ensure_schema(con)
    ch = add_chain(con, "T")
    rep = user_row(con, add_user(con, "reporter"))
    now = timeutil.now()
    form = {"kind": "near_miss", "occurred_at": now.strftime("%Y-%m-%dT%H:%M"), "risk_area_id": str(ch["a"]), "title": "ตอนนี้", "likelihood": "1", "severity": "1"}
    assert incidents.report(con, rep, form)[1] == []


# ---------- แก้ระดับการบาดเจ็บ ----------
class W:
    def __init__(self, db):
        self.db = db
        self.ch = add_chain(db, "R")
        u = lambda role, name: user_row(db, add_user(db, role, name))
        self.rep, self.inv, self.admin, self.viewer = u("reporter", "ผู้แจ้ง"), u("assessor", "ผู้สอบสวน"), u("admin", "แอดมิน"), u("viewer", "ผู้ดู")

    def new(self, sev="first_aid", kind="accident"):
        iid, errors = incidents.report(self.db, self.rep, {"kind": kind, "occurred_at": "2026-03-09T08:00", "risk_area_id": str(self.ch["a"]),
                                                           "title": "เหตุ", "injury_severity_id": str(severity_id(self.db, sev)), "likelihood": "2", "severity": "2"})
        assert errors == []
        return iid


@pytest.fixture
def w(tmp_path, fixed):
    from safety.schema import ensure_schema
    con = connect(str(tmp_path / "s.db")); ensure_schema(con)
    return W(con)


def test_reclassify_changes_severity_with_history_and_moves_the_kpi(w):
    iid = w.new("first_aid")
    assert incident_stats.days_without_lost_time(w.db, w.admin) == (None, None)
    before = w.db.execute("SELECT version FROM incidents WHERE id=?", (iid,)).fetchone()[0]
    assert flow.reclassify(w.db, w.inv, iid, severity_id(w.db, "lost_time"), "แพทย์ให้หยุดงาน 3 วัน", "3") == []
    row = w.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    assert (row["injury_severity_id"], row["lost_days"], row["version"], row["status"]) == (severity_id(w.db, "lost_time"), 3, before + 1, "reported")
    ev = w.db.execute("SELECT * FROM incident_events WHERE kind='reclassified'").fetchone()
    assert ev["note"] == "แพทย์ให้หยุดงาน 3 วัน" and f'"old": {severity_id(w.db, "first_aid")}' in ev["data"] and f'"new": {severity_id(w.db, "lost_time")}' in ev["data"]
    assert incident_stats.days_without_lost_time(w.db, w.admin) == (1, "2026-03-09")


def test_reclassify_after_closing_is_allowed_but_not_after_rejecting_and_not_for_near_miss(w):
    iid = w.new()
    w.db.execute("UPDATE incidents SET status='closed' WHERE id=?", (iid,)); w.db.commit()
    assert flow.reclassify(w.db, w.admin, iid, severity_id(w.db, "medical"), "ปรับระดับ", "") == []
    w.db.execute("UPDATE incidents SET status='rejected' WHERE id=?", (iid,)); w.db.commit()
    assert flow.reclassify(w.db, w.admin, iid, severity_id(w.db, "lost_time"), "x", "1")
    nm = w.new(kind="near_miss")
    assert flow.reclassify(w.db, w.admin, nm, severity_id(w.db, "lost_time"), "x", "1")


def test_reclassify_validation_permission_and_stale_version(w):
    iid = w.new()
    lt = severity_id(w.db, "lost_time")
    assert flow.reclassify(w.db, w.rep, iid, lt, "x", "1") == ["คุณไม่มีสิทธิ์ปรับระดับความรุนแรง"]
    for args in ((9999, "x", ""), ("abc", "x", ""), (lt, "  ", "1"), (lt, "ก" * 501, "1"), (lt, "x", "abc"), (lt, "x", "-1"), (lt, "x", "99999"),
                 (severity_id(w.db, "first_aid"), "เท่าเดิม", "")):
        assert flow.reclassify(w.db, w.inv, iid, *args), args
    w.db.execute("UPDATE injury_severities SET active=0 WHERE code='medical'"); w.db.commit()
    assert flow.reclassify(w.db, w.inv, iid, severity_id(w.db, "medical"), "x", "")
    assert flow.reclassify(w.db, w.inv, iid, lt, "ต้องมีวันหยุด", "")                                  # ระดับหยุดงานต้องระบุจำนวนวัน
    seen = w.db.execute("SELECT version FROM incidents WHERE id=?", (iid,)).fetchone()[0]
    assert flow.reclassify(w.db, w.inv, iid, lt, "ok", "2", expected_version=seen) == []
    assert flow.reclassify(w.db, w.admin, iid, severity_id(w.db, "fatality"), "ซ้อน", "9", expected_version=seen) == flow.STALE_FORM
    other = user_row(w.db, add_user(w.db, "assessor", "นอกขอบเขต", factory_id=w.db.execute("INSERT INTO factories(code,name_th) VALUES('ZZ','ซ')").lastrowid))
    assert flow.reclassify(w.db, other, iid, lt, "x", "1") == flow.NOT_FOUND


def test_reclassify_screen_and_audit_visible(safety_app, fixed):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "V")
    c, rep = login(safety_app, "assessor"), login(safety_app, "reporter")
    iid, _ = incidents.report(con, user_row(con, rep.uid), {"kind": "accident", "occurred_at": "2026-03-09T08:00", "risk_area_id": str(ch["a"]),
                                                            "title": "เหตุ", "injury_severity_id": str(severity_id(con, "first_aid"))})
    page = c.get(f"/incidents/{iid}").get_data(as_text=True)
    assert f'action="/incidents/{iid}/reclassify"' in page and re.search(r'name="version" value="0"', page)
    assert f'action="/incidents/{iid}/reclassify"' not in rep.get(f"/incidents/{iid}").get_data(as_text=True)
    assert c.post(f"/incidents/{iid}/reclassify", data={"severity_id": "x"}).status_code == 400               # ต้องมี CSRF
    r = c.post(f"/incidents/{iid}/reclassify", data={"_csrf": csrf(c), "injury_severity_id": str(severity_id(con, "lost_time")), "reason": "หยุดงาน",
                                                      "lost_days": "4", "version": "0"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "ปรับระดับความรุนแรง" in html and "หยุดงาน 4 วัน" in html
    assert rep.post(f"/incidents/{iid}/reclassify", data={"_csrf": csrf(rep)}).status_code == 403


# ---------- ช่องผู้เกี่ยวข้อง 20 ช่อง ----------
def test_new_form_has_20_people_slots_five_visible_rest_collapsed(safety_app):
    c = login(safety_app, "reporter")
    html = c.get("/incidents/new").get_data(as_text=True)
    assert 'name="people_name_19"' in html and 'name="people_name_20"' not in html
    head, _, rest = html.partition("<details>")
    assert 'name="people_name_4"' in head and 'name="people_name_5"' not in head and 'name="people_name_5"' in rest
