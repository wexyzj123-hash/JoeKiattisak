from datetime import date, datetime

import pytest

from safety import plant, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_user, severity_id

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


def test_empty_database_gives_zeros_and_no_lost_time_date(db):
    d = plant.plant_summary(db)
    assert d["days_without_lost_time"] is None and d["last_lost_time_date"] is None
    assert (d["open_hazards"], d["overdue"], d["high_risk_open"], d["capa_open"], d["capa_overdue"]) == (0, 0, 0, 0, 0)
    assert d["incidents_month"] == {"accident": 0, "near_miss": 0} and d["patrol_avg_score_30d"] is None
    assert len(d["monthly"]) == 12 and d["monthly"][-1]["m"] == "2026-03" and d["monthly"][0]["m"] == "2025-04"
    assert all(set(m) == {"m", "reported", "closed", "accidents", "near_miss"} and not any(v for k, v in m.items() if k != "m") for m in d["monthly"])


def test_numbers_match_seed_and_exclude_rejected(db):
    ch = add_chain(db, "A"); u = add_user(db, "admin", "x")
    lost = db.execute("SELECT id FROM injury_severities WHERE counts_lost_time=1 LIMIT 1").fetchone()[0]
    add_hazard(db, ch["a"], u, status="assigned", reported_at="2026-03-02 08:00", due_date="2026-03-10")
    add_hazard(db, ch["a"], u, status="rejected", reported_at="2026-03-03 08:00")
    h = add_hazard(db, ch["a"], u, status="closed", reported_at="2026-02-02 08:00")
    db.execute("INSERT INTO hazard_events(hazard_id, at, kind, from_status, to_status) VALUES(?,?,?,?,?)", (h, "2026-03-05 09:00", "closed", "assigned", "closed"))
    add_incident(db, ch["a"], u, occurred_at="2026-03-10 09:00", injury_severity_id=lost)
    add_incident(db, ch["a"], u, kind="near_miss", occurred_at="2026-03-11 09:00")
    add_incident(db, ch["a"], u, occurred_at="2026-03-12 09:00", status="rejected", injury_severity_id=lost)
    db.commit()
    d = plant.plant_summary(db)
    assert (d["open_hazards"], d["overdue"]) == (1, 1)
    assert d["incidents_month"] == {"accident": 1, "near_miss": 1}
    assert d["days_without_lost_time"] == 10 and d["last_lost_time_date"] == "2026-03-10"
    mar = d["monthly"][-1]
    assert (mar["reported"], mar["closed"], mar["accidents"], mar["near_miss"]) == (1, 1, 1, 1)
    assert d["hazards_month"] == 1 and d["closed_month"] == 1


def test_capa_and_patrol_numbers(db):
    ch = add_chain(db, "A"); u = add_user(db, "admin", "x")
    cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('C','ก')").lastrowid
    for i, (st, due) in enumerate((("open", "2026-03-01"), ("in_progress", "2026-04-01"), ("verified", "2026-03-01"), ("cancelled", "2026-03-01"))):
        db.execute("INSERT INTO capas(code, source_kind, action_type, title, risk_area_id, owner_id, due_date, status, created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                   (f"CP-{i}", "manual", "corrective", "t", ch["a"], u, due, st, u))
    for i, (score, at) in enumerate(((80, "2026-03-10 10:00:00"), (60, "2026-03-15 10:00:00"), (10, "2025-12-01 10:00:00"))):
        db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status, submitted_at, score_pct) VALUES(?,?,?,?,?,?,?)",
                   (f"PT-{i}", cl, ch["a"], u, "draft", None, None))
        db.execute("UPDATE patrol_rounds SET status='submitted', submitted_at=?, score_pct=? WHERE code=?", (at, score, f"PT-{i}"))
    db.commit()
    d = plant.plant_summary(db)
    assert (d["capa_open"], d["capa_overdue"]) == (2, 1) and d["patrol_avg_score_30d"] == 70


def test_api_dashboard_requires_service_token(safety_app):
    c = safety_app.test_client()
    assert c.get("/api/dashboard").status_code == 401 and c.get("/api/dashboard", headers={"X-Service-Token": "nope"}).status_code == 401
    r = c.get("/api/dashboard", headers={"X-Service-Token": "svc-token"})
    assert r.status_code == 200 and r.get_json()["open_hazards"] == 0 and "monthly" in r.get_json()
    assert c.get("/api/summary", headers={"X-Service-Token": "svc-token"}).get_json() == {"open_hazards": 0, "overdue": 0}
