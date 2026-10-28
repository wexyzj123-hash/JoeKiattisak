import re
from datetime import date, datetime

import pytest

from safety import incident_stats as ist
from safety import timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_incident, add_user, severity_id, user_row
from tests.safety.helpers import login

TODAY = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)
    monkeypatch.setattr(timeutil, "now", lambda: datetime(2026, 3, 10, 12, 0))


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        self.rep = add_user(db, "reporter", "ผู้แจ้ง")
        self.admin = user_row(db, add_user(db, "admin", "แอดมิน"))
        self.scoped_b = user_row(db, add_user(db, "assessor", "B", factory_id=self.b["f"]))
        self.n = 0

    def inc(self, when="2026-03-01 09:00", sev="first_aid", chain="a", **kw):
        self.n += 1
        kind = kw.pop("kind", "accident")
        extra = {"injury_severity_id": severity_id(self.db, sev)} if kind == "accident" else {}
        return add_incident(self.db, getattr(self, chain)["a"], self.rep, code=f"IN-S-{self.n:04d}", occurred_at=when, kind=kind, **extra, **kw)


@pytest.fixture
def w(db):
    return W(db)


def test_days_counts_from_last_lost_time_date_today_is_zero_leap_and_year_boundary(w):
    assert ist.days_without_lost_time(w.db, w.admin) == (None, None)
    w.inc("2026-03-04 23:59", "lost_time")
    assert ist.days_without_lost_time(w.db, w.admin) == (6, "2026-03-04")
    w.inc("2026-03-10 00:01", "fatality")
    assert ist.days_without_lost_time(w.db, w.admin) == (0, "2026-03-10")                       # วันนี้ = 0
    assert ist.days_without_lost_time(w.db, w.admin, date(2026, 3, 12)) == (2, "2026-03-10")
    w.db.execute("DELETE FROM incident_events"); w.db.execute("DELETE FROM incidents"); w.db.commit()
    w.inc("2024-02-29 08:00", "permanent")
    assert ist.days_without_lost_time(w.db, w.admin, date(2025, 2, 28)) == (365, "2024-02-29")
    assert ist.days_without_lost_time(w.db, w.admin, date(2025, 1, 1)) == (307, "2024-02-29")


def test_rejected_non_lost_time_and_near_miss_are_ignored(w):
    w.inc("2026-03-09 10:00", "medical"); w.inc("2026-03-09 11:00", "first_aid"); w.inc("2026-03-09 12:00", kind="near_miss")
    rej = w.inc("2026-03-08 10:00", "lost_time")
    w.db.execute("UPDATE incidents SET status='rejected' WHERE id=?", (rej,)); w.db.commit()
    assert ist.days_without_lost_time(w.db, w.admin) == (None, None)
    w.inc("2026-02-20 10:00", "lost_time")
    assert ist.days_without_lost_time(w.db, w.admin) == (18, "2026-02-20")


def test_live_flag_toggle_changes_kpi_and_deactivated_severity_still_counts(w):
    w.inc("2026-03-05 10:00", "medical")
    assert ist.days_without_lost_time(w.db, w.admin) == (None, None)
    w.db.execute("UPDATE injury_severities SET counts_lost_time=1 WHERE code='medical'"); w.db.commit()
    assert ist.days_without_lost_time(w.db, w.admin) == (5, "2026-03-05")
    w.db.execute("UPDATE injury_severities SET active=0 WHERE code='medical'"); w.db.commit()
    assert ist.days_without_lost_time(w.db, w.admin) == (5, "2026-03-05")                        # ปิดใช้งานแล้วข้อมูลเดิมยังนับ
    w.db.execute("UPDATE injury_severities SET counts_lost_time=0 WHERE code='medical'"); w.db.commit()
    assert ist.days_without_lost_time(w.db, w.admin) == (None, None)


def test_scope_limits_days_to_factory_and_unscoped_is_company_wide(w):
    w.inc("2026-03-08 10:00", "lost_time", chain="a"); w.inc("2026-03-01 10:00", "lost_time", chain="b")
    assert ist.days_without_lost_time(w.db, w.admin) == (2, "2026-03-08")
    assert ist.days_without_lost_time(w.db, w.scoped_b) == (9, "2026-03-01")


def test_counts_monthly_by_severity_and_by_area(w):
    w.inc("2026-03-02 10:00", "first_aid"); w.inc("2026-03-03 10:00", "lost_time"); w.inc("2026-02-03 10:00", "first_aid", chain="b")
    w.inc("2026-03-04 10:00", kind="near_miss"); w.inc("2025-12-31 23:59", kind="near_miss"); w.inc("2020-01-01 10:00", "first_aid")
    rej = w.inc("2026-03-05 10:00", "first_aid"); w.db.execute("UPDATE incidents SET status='rejected' WHERE id=?", (rej,)); w.db.commit()
    assert ist.counts_by_kind_status(w.db, w.admin)["accident"]["reported"] == 4 and ist.counts_by_kind_status(w.db, w.admin)["near_miss"]["reported"] == 2
    trend = ist.monthly_incidents(w.db, w.admin, months=4)
    assert trend == [("2025-12", 0, 1), ("2026-01", 0, 0), ("2026-02", 1, 0), ("2026-03", 2, 1)]
    assert len(ist.monthly_incidents(w.db, w.admin)) == 12
    sev = ist.by_severity(w.db, w.admin)
    assert sev == [("ปฐมพยาบาล", 3), ("หยุดงาน", 1)]                                            # ไม่รวม Near Miss/ปฏิเสธ; เรียงตามลำดับ rank
    assert ist.by_area(w.db, w.admin) == [("พื้นที่ A", 5), ("พื้นที่ B", 1)]
    assert ist.monthly_incidents(w.db, w.scoped_b, months=4)[2] == ("2026-02", 1, 0) and ist.by_severity(w.db, w.scoped_b) == [("ปฐมพยาบาล", 1)]


def test_stats_page_requires_permission_and_has_no_names(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "P")
    rep = add_user(con, "reporter", "ผู้แจ้ง")
    iid = add_incident(con, ch["a"], rep, injury_severity_id=severity_id(con, "lost_time"))
    con.execute("INSERT INTO incident_people(incident_id, role, name, employee_no) VALUES(?,?,?,?)", (iid, "injured", "สมชาย ใจดี", "EMP-7788"))
    con.commit()
    assert login(safety_app, "reporter").get("/incidents/stats").status_code == 403
    assert login(safety_app, "viewer").get("/incidents/stats").status_code == 403
    html = login(safety_app, "admin").get("/incidents/stats").get_data(as_text=True)
    assert "สถิติอุบัติเหตุ" in html and "สมชาย" not in html and "EMP-7788" not in html and 'role="img"' in html
    assert "หยุดงาน" in html and "วันปลอดอุบัติเหตุ" in html


def test_dashboard_kpi_card_number_text_and_scope(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "K")
    rep = add_user(con, "reporter", "ผู้แจ้ง")
    c = login(safety_app, "reporter")
    html = c.get("/").get_data(as_text=True)
    assert 'data-kpi="days-no-lti">-<' in html and "ยังไม่มีอุบัติเหตุหยุดงานที่บันทึก" in html and re.search(r"data-kpi=\"days-no-lti\"[^>]*>\d", html) is None
    add_incident(con, ch["a"], rep, occurred_at="2026-03-04 10:00", injury_severity_id=severity_id(con, "lost_time"))
    for role in ("reporter", "viewer", "admin"):                                                # ทุกคนเห็นตัวเลขระดับองค์กร (ไม่มี PII)
        html = login(safety_app, role).get("/").get_data(as_text=True)
        assert re.search(r'data-kpi="days-no-lti"[^>]*>6<', html), role
    assert len(re.findall(r'class="kpi[ "]', login(safety_app, "admin").get("/").get_data(as_text=True))) == 5


def test_stats_query_count_constant_with_many_incidents(safety_app):
    import sqlite3
    from safety import db as dbmod

    def run(n):
        con = connect(safety_app.config["DATABASE"])
        ch = add_chain(con, f"Q{n}")
        rep = add_user(con, "reporter", f"คน{n}")
        for i in range(n):
            add_incident(con, ch["a"], rep, code=f"IN-Q{n}-{i}", injury_severity_id=severity_id(con, "first_aid"))
        con.close()
        c = login(safety_app, "admin")
        statements, real = [], dbmod.connect
        def traced(path):
            con2 = real(path); con2.set_trace_callback(statements.append); return con2
        dbmod.connect = traced
        try:
            c.get("/incidents/stats")
        finally:
            dbmod.connect = real
        return len([s for s in statements if s.strip().upper().startswith("SELECT")])
    small, large = run(3), run(150)
    assert small == large and small > 0
