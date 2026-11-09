from datetime import date, datetime

import pytest

from safety import dash, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_refs, add_user, user_row

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
        self.t1, _ = add_refs(db)
        self.t2 = db.execute("INSERT INTO hazard_types(code,name_th) VALUES('T2','ไฟฟ้า')").lastrowid
        u = lambda role, name, **kw: user_row(db, add_user(db, role, name, **kw))
        self.admin, self.rep = u("admin", "แอดมิน"), u("reporter", "ผู้แจ้ง")
        self.mgr_a = u("assessor", "ผู้จัดการ A", factory_id=self.a["f"])
        self.h1 = add_hazard(db, self.a["a"], self.rep["id"], code="HZ-1", status="assigned", reported_at="2026-03-02 08:00", due_date="2026-03-05")
        self.h2 = add_hazard(db, self.a["a"], self.rep["id"], code="HZ-2", status="closed", reported_at="2026-02-02 08:00", hazard_type_id=self.t2)
        self.h3 = add_hazard(db, self.b["a"], self.rep["id"], code="HZ-3", status="reported", reported_at="2026-03-03 08:00")
        for hid, at, kind, to in ((self.h1, "2026-03-02 08:00", "reported", "reported"), (self.h2, "2026-03-12 09:00", "closed", "closed"), (self.h3, "2026-03-03 08:00", "reported", "reported")):
            db.execute("INSERT INTO hazard_events(hazard_id, at, kind, to_status) VALUES(?,?,?,?)", (hid, at, kind, to))
        self.inc = add_incident(db, self.a["a"], self.rep["id"], code="IN-1", occurred_at="2026-03-10 09:00")
        db.execute("INSERT INTO incident_events(incident_id, at, kind, to_status) VALUES(?,?,?,?)", (self.inc, "2026-03-10 10:00", "reported", "reported"))
        db.commit()


@pytest.fixture
def w(db):
    return W(db)


def test_home_data_kpis_series_delta_and_pipeline(w):
    d = dash.home_data(w.db, w.admin)
    assert d["kpi"]["reported"] == {"value": 2, "prev": 1, "delta_pct": 100.0, "series": [0] * 10 + [1, 2]}
    assert d["kpi"]["closed"]["value"] == 1 and d["kpi"]["closed"]["series"][-1] == 1
    assert d["pipeline"] == {"reported": 1, "assessed": 0, "assigned": 1, "closed_month": 1}
    assert [m["m"] for m in d["months"]][-1] == "2026-03" and d["months"][-1] == {"m": "2026-03", "reported": 2, "closed": 1, "accidents": 1, "near_miss": 0}


def test_home_data_is_scoped(w):
    d = dash.home_data(w.db, w.mgr_a)
    assert d["kpi"]["reported"]["value"] == 1 and d["pipeline"]["reported"] == 0 and d["pipeline"]["assigned"] == 1
    assert all("HZ-3" not in str(x) for x in d["activity"]) and all("พื้นที่ B" not in x["name"] for x in d["top_areas"])


def test_top_types_and_areas_open_only_with_filter_ids(w):
    d = dash.home_data(w.db, w.admin)
    assert d["top_types"] == [{"id": w.t1, "name": "สารเคมี", "n": 2}] and {a["n"] for a in d["top_areas"]} == {1}
    assert all(set(x) == {"id", "name", "n"} for x in d["top_types"] + d["top_areas"])


def test_activity_feed_mixes_kinds_sorted_newest_first_with_links(w):
    items = dash.home_data(w.db, w.admin)["activity"]
    assert [i["code"] for i in items][:2] == ["HZ-2", "IN-1"] and items[0]["label"] == "ปิดรายการ" and items[0]["kind"] == "hazard"
    assert items[1]["kind"] == "incident" and items[1]["href"] == f"/incidents/{w.inc}" and items[0]["href"] == f"/hazards/{w.h2}"
    assert all(i["at"] for i in items) and len(items) <= 10


def test_reporter_activity_limited_to_own_items(w):
    other = user_row(w.db, add_user(w.db, "reporter", "คนอื่น"))
    assert dash.home_data(w.db, other)["activity"] == [] and len(dash.home_data(w.db, w.rep)["activity"]) >= 2
