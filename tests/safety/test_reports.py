from datetime import date, datetime

import pytest

from safety import reports, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_user, severity_id, user_row

NOW = datetime(2026, 3, 20, 10, 0)


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
    monkeypatch.setattr(timeutil, "today", lambda: NOW.date())


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        u = lambda role, name, **kw: user_row(db, add_user(db, role, name, **kw))
        self.admin = u("admin", "แอดมิน")
        self.mgr_a = u("assessor", "ผู้จัดการ A", factory_id=self.a["f"])
        self.rep = u("reporter", "ผู้แจ้ง")
        # มีนาคม 2026: A มี 2 อันตราย (1 ปิดแล้ว), B มี 1; ก.พ.: A 1
        self.h1 = add_hazard(db, self.a["a"], self.rep["id"], status="closed", reported_at="2026-03-02 08:00")
        self.h2 = add_hazard(db, self.a["a"], self.rep["id"], status="assigned", reported_at="2026-03-05 08:00", due_date="2026-03-10")
        add_hazard(db, self.b["a"], self.rep["id"], reported_at="2026-03-06 08:00")
        h_feb = add_hazard(db, self.a["a"], self.rep["id"], reported_at="2026-02-06 08:00", status="closed")
        db.execute("INSERT INTO hazard_events(hazard_id, at, kind, from_status, to_status) VALUES(?,?,?,?,?)", (h_feb, "2026-02-20 09:00", "closed", "assigned", "closed"))
        db.execute("INSERT INTO hazard_events(hazard_id, at, kind, from_status, to_status) VALUES(?,?,?,?,?)", (self.h1, "2026-03-12 09:00", "closed", "assigned", "closed"))
        self.lost = severity_id(db, "lost_time") if db.execute("SELECT 1 FROM injury_severities WHERE code='lost_time'").fetchone() else None
        if self.lost is None:
            self.lost = db.execute("SELECT id FROM injury_severities WHERE counts_lost_time=1 LIMIT 1").fetchone()[0]
        add_incident(db, self.a["a"], self.rep["id"], occurred_at="2026-03-03 10:00", injury_severity_id=self.lost)
        add_incident(db, self.a["a"], self.rep["id"], kind="near_miss", occurred_at="2026-03-04 10:00")
        add_incident(db, self.b["a"], self.rep["id"], occurred_at="2026-03-07 10:00", injury_severity_id=self.lost)
        db.commit()

    def kpi(self, rep, label):
        return next(k.value for k in rep.kpis if k.label == label)


@pytest.fixture
def w(db):
    return W(db)


def test_monthly_counts_match_seed(w):
    r = reports.monthly_department(w.db, w.admin, 2026, 3)
    assert (w.kpi(r, "แจ้งอันตราย"), w.kpi(r, "ปิดแล้ว"), w.kpi(r, "อุบัติเหตุ"), w.kpi(r, "Near Miss"), w.kpi(r, "อุบัติเหตุหยุดงาน")) == (3, 1, 2, 1, 2)
    assert w.kpi(r, "ค้างปลายเดือน") == 2 and w.kpi(r, "เกินกำหนดปลายเดือน") == 1
    assert r.period_label == "มีนาคม 2569" and len(r.tables) == 4


def test_scoped_user_numbers_exclude_other_factory(w):
    r = reports.monthly_department(w.db, w.mgr_a, 2026, 3)
    assert w.kpi(r, "แจ้งอันตราย") == 2 and w.kpi(r, "อุบัติเหตุ") == 1 and w.kpi(r, "อุบัติเหตุหยุดงาน") == 1
    rows = [c for t in r.tables for row in t.rows for c in row]
    assert "พื้นที่ B" not in rows and all("B" not in str(c) for c in rows if isinstance(c, str) and c.startswith("IN-"))


def test_monthly_department_outside_scope_returns_none(w):
    assert reports.monthly_department(w.db, w.mgr_a, 2026, 3, w.b["d"]) is None
    assert reports.monthly_department(w.db, w.mgr_a, 2026, 3, 9999) is None
    r = reports.monthly_department(w.db, w.admin, 2026, 3, w.b["d"])
    assert w.kpi(r, "แจ้งอันตราย") == 1 and "แผนก B" in r.subtitle


def test_empty_period_returns_zero_kpis_and_empty_tables(w):
    r = reports.monthly_department(w.db, w.admin, 2025, 1)
    assert w.kpi(r, "แจ้งอันตราย") == 0 and w.kpi(r, "คะแนนตรวจเฉลี่ย") == "-"
    assert all(t.rows == [] for t in r.tables)
    assert reports.annual_summary(w.db, w.admin, 2020).tables[0].rows[-1] == ["รวมทั้งปี", 0, 0, 0, 0, 0]
    assert reports.capa_status(w.db, w.admin).kpis[0].value == 0


def test_incident_tables_never_contain_people_names(w):
    iid = w.db.execute("SELECT id FROM incidents LIMIT 1").fetchone()[0]
    cols = [r[1] for r in w.db.execute("PRAGMA table_info(incident_people)")]
    name_col = "name" if "name" in cols else next(c for c in cols if "name" in c)
    vals = {c: "x" for c in cols if c not in ("id",)}
    vals["incident_id"] = iid
    if "role" in vals:
        vals["role"] = "injured"
    vals[name_col] = "สมชาย ใจดีลับ"
    w.db.execute(f"INSERT INTO incident_people({','.join(vals)}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
    w.db.commit()
    for rep in (reports.monthly_department(w.db, w.admin, 2026, 3), reports.annual_summary(w.db, w.admin, 2026),
                reports.incident_statistics(w.db, w.admin, 2026), reports.capa_status(w.db, w.admin)):
        text = repr(rep)
        assert "สมชาย" not in text


def test_annual_has_12_rows_and_totals_equal_sum(w):
    r = reports.annual_summary(w.db, w.admin, 2026)
    monthly = r.tables[0].rows
    assert len(monthly) == 13
    assert monthly[-1][1:] == [sum(row[i] for row in monthly[:-1]) for i in range(1, 6)]
    assert monthly[-1][1] == 4 and monthly[1][1] == 1                       # ก.พ. มี 1, ทั้งปี 4


def test_incident_statistics_breakdowns(w):
    r = reports.incident_statistics(w.db, w.admin, 2026)
    assert w.kpi(r, "อุบัติเหตุ") == 2 and w.kpi(r, "Near Miss") == 1 and r.tables[0].rows[2] == ["มีนาคม", 2, 1]
    scoped = reports.incident_statistics(w.db, w.mgr_a, 2026)
    assert w.kpi(scoped, "อุบัติเหตุ") == 1


def test_capa_status_overdue_list_and_average_days(w):
    mk = lambda code, status, due, **kw: w.db.execute(
        "INSERT INTO capas(code, source_kind, action_type, title, risk_area_id, owner_id, due_date, status, created_by, created_at, verified_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (code, "manual", "corrective", "t", w.a["a"], w.rep["id"], due, status, w.admin["id"], kw.get("c", "2026-03-01 08:00"), kw.get("v")))
    mk("CP-1", "open", "2026-03-10"); mk("CP-2", "verified", "2026-03-30", v="2026-03-11 08:00"); mk("CP-3", "open", "2026-03-30")
    w.db.commit()
    r = reports.capa_status(w.db, w.admin)
    assert w.kpi(r, "รอดำเนินการ") == 2 and w.kpi(r, "ยืนยันผลแล้ว") == 1 and w.kpi(r, "เกินกำหนด") == 1
    assert r.tables[0].rows == [["CP-1", "t", "ผู้แจ้ง", "2026-03-10", 10]] and w.kpi(r, "เวลาเฉลี่ยจนยืนยันผล (วัน)") == "10.0"
    assert w.kpi(reports.capa_status(w.db, w.mgr_a), "รอดำเนินการ") == 2 and w.kpi(reports.capa_status(w.db, user_row(w.db, add_user(w.db, "admin", "x", factory_id=w.b["f"]))), "รอดำเนินการ") == 0


def test_year_and_month_validation(w):
    for bad in ((2026, 0), (2026, 13), (1900, 1)):
        with pytest.raises(ValueError):
            reports.monthly_department(w.db, w.admin, *bad)
    with pytest.raises(ValueError):
        reports.annual_summary(w.db, w.admin, 3000)
    assert reports.build(w.db, w.admin, "nope", 2026, 3) is None


def test_rejected_hazards_and_cancelled_capas_not_counted(w):
    add_hazard(w.db, w.a["a"], w.rep["id"], status="rejected", reported_at="2026-03-08 08:00")
    w.db.execute("INSERT INTO capas(code, source_kind, action_type, title, risk_area_id, owner_id, due_date, status, created_by, created_at) VALUES('CP-X','manual','corrective','t',?,?,?,?,?,?)",
                 (w.a["a"], w.rep["id"], "2026-04-01", "cancelled", w.admin["id"], "2026-03-02 08:00"))
    w.db.commit()
    r = reports.monthly_department(w.db, w.admin, 2026, 3)
    assert w.kpi(r, "แจ้งอันตราย") == 3 and w.kpi(r, "CAPA ที่เปิด") == 0


def test_hazard_closed_in_later_month_counts_open_at_month_end(w):
    assert w.kpi(reports.monthly_department(w.db, w.admin, 2026, 3, w.a["d"]), "ค้างปลายเดือน") == 1                 # h2 เท่านั้น; h1 ปิด 12 มี.ค.
    w.db.execute("INSERT INTO hazard_events(hazard_id, at, kind, from_status, to_status) VALUES(?,?,?,?,?)", (w.h2, "2026-04-02 09:00", "closed", "assigned", "closed"))
    w.db.commit()
    assert w.kpi(reports.monthly_department(w.db, w.admin, 2026, 3, w.a["d"]), "ค้างปลายเดือน") == 1                 # ปิดเดือนเมษา → มีนายังค้าง
    assert w.kpi(reports.monthly_department(w.db, w.admin, 2026, 4, w.a["d"]), "ค้างปลายเดือน") == 0
