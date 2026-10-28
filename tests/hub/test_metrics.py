from datetime import date

import pytest

from hub import metrics
from hub.db import connect
from hub.schema import ensure_schema
from tests.conftest import csrf
from tests.hub.test_launcher import login_as

TODAY = date(2026, 3, 20)


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "h.db"))
    ensure_schema(con)
    metrics.seed_defaults(con)
    return con


def test_default_indicators_seeded_once_and_editable(db):
    zones = {r["zone"] for r in db.execute("SELECT zone FROM metrics")}
    assert zones == {"env", "utility"} and db.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] >= 8
    db.execute("UPDATE metrics SET active=0 WHERE key='electricity'"); db.execute("DELETE FROM metrics WHERE key='water'"); db.commit()
    metrics.seed_defaults(db)
    assert db.execute("SELECT active FROM metrics WHERE key='electricity'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM metrics WHERE key='water'").fetchone()[0] == 0


def test_zone_view_latest_delta_trace_and_status(db):
    db.execute("UPDATE metrics SET target=100, direction='lower' WHERE key='electricity'"); db.commit()
    for d, v in (("2026-01-01", 90), ("2026-02-01", 100), ("2026-03-01", 108)):
        assert metrics.save_reading(db, {"id": 1}, "electricity", d, str(v), today=TODAY) == []
    row = next(m for m in metrics.zone_view(db, "utility", TODAY) if m["key"] == "electricity")
    assert (row["latest"], row["prev"], row["latest_date"]) == (108.0, 100.0, "2026-03-01") and row["trace"] == [90.0, 100.0, 108.0]
    assert row["delta_pct"] == 8.0 and row["status"] == "warn"
    metrics.save_reading(db, {"id": 1}, "electricity", "2026-03-15", "130", today=TODAY)
    assert next(m for m in metrics.zone_view(db, "utility", TODAY) if m["key"] == "electricity")["status"] == "bad"
    metrics.save_reading(db, {"id": 1}, "electricity", "2026-03-15", "95", today=TODAY)                # แก้ค่าวันเดิม = ทับ
    assert db.execute("SELECT COUNT(*) FROM metric_readings WHERE read_date='2026-03-15'").fetchone()[0] == 1
    assert next(m for m in metrics.zone_view(db, "utility", TODAY) if m["key"] == "electricity")["status"] == "ok"


def test_higher_is_better_and_no_target_and_no_data(db):
    db.execute("UPDATE metrics SET target=60, direction='higher' WHERE key='recycle_rate'"); db.commit()
    metrics.save_reading(db, {"id": 1}, "recycle_rate", "2026-03-01", "50", today=TODAY)
    zv = {m["key"]: m for m in metrics.zone_view(db, "env", TODAY)}
    assert zv["recycle_rate"]["status"] in ("warn", "bad") and zv["hazardous_waste"]["status"] == "none" and zv["hazardous_waste"]["latest"] is None


def test_inactive_metrics_hidden(db):
    db.execute("UPDATE metrics SET active=0 WHERE key='water'"); db.commit()
    assert "water" not in {m["key"] for m in metrics.zone_view(db, "utility", TODAY)}


@pytest.mark.parametrize("key,date_,val", [("nope", "2026-03-01", "1"), ("electricity", "2026-13-01", "1"), ("electricity", "not-a-date", "1"), ("electricity", "2026-03-01", "abc"),
                                          ("electricity", "2026-03-01", "nan"), ("electricity", "2026-03-01", "inf"), ("electricity", "2026-03-01", "-5"), ("electricity", "2026-04-30", "1"), ("electricity", "2026-03-01", "1e99")])
def test_reading_validation(db, key, date_, val):
    assert metrics.save_reading(db, {"id": 1}, key, date_, val, today=TODAY)
    assert db.execute("SELECT COUNT(*) FROM metric_readings").fetchone()[0] == 0


def test_csv_import_reports_bad_rows_and_keeps_good(db):
    text = "metric,date,value\nelectricity,2026-03-01,1200\nwater,2026-03-01,45.5\nbogus,2026-03-01,1\nwater,2026-03-02,x\n\nelectricity,2026-03-02,1,300"
    ok, errors = metrics.import_csv(db, {"id": 1}, text, today=TODAY)
    assert ok == 2 and len(errors) == 3 and any("บรรทัด 4" in e for e in errors)
    assert db.execute("SELECT COUNT(*) FROM metric_readings").fetchone()[0] == 2
    assert metrics.import_csv(db, {"id": 1}, "x" * 600_000, today=TODAY)[0] == 0


def test_save_metric_validation(db):
    ok = {"key": "steam", "zone": "utility", "name_th": "ไอน้ำ", "unit": "ตัน", "direction": "lower", "target": "50", "decimals": "1", "sort": "90"}
    assert metrics.save_metric(db, ok) == []
    assert db.execute("SELECT target, decimals FROM metrics WHERE key='steam'").fetchone()[:] == (50.0, 1)
    assert metrics.save_metric(db, {**ok, "name_th": "ไอน้ำใหม่", "target": ""}) == [] and db.execute("SELECT target, name_th FROM metrics WHERE key='steam'").fetchone()[:] == (None, "ไอน้ำใหม่")
    for bad in ({"key": "A B"}, {"zone": "x"}, {"name_th": ""}, {"direction": "up"}, {"target": "abc"}, {"decimals": "9"}, {"unit": "x" * 40}):
        assert metrics.save_metric(db, {**ok, "key": "tmp1", **bad}), bad


def test_admin_page_roles_and_csrf(hub_app):
    for role in ("technician", "viewer"):
        assert login_as(hub_app, role, role).get("/admin/metrics").status_code == 403
    sup = login_as(hub_app, "sup", "supervisor")
    assert sup.get("/admin/metrics").status_code == 200
    r = sup.post("/admin/metrics", data={"_csrf": csrf(sup), "action": "reading", "key": "electricity", "date": "2026-03-01", "value": "1000"})
    assert r.status_code == 302
    con = connect(hub_app.config["DATABASE"])
    assert con.execute("SELECT value FROM metric_readings").fetchone()[0] == 1000.0
    sup.post("/admin/metrics", data={"_csrf": csrf(sup), "action": "toggle", "key": "electricity"})                # หัวหน้ากะแก้ตัวชี้วัดไม่ได้
    assert con.execute("SELECT active FROM metrics WHERE key='electricity'").fetchone()[0] == 1
    adm = login_as(hub_app, "adm", "admin")
    adm.post("/admin/metrics", data={"_csrf": csrf(adm), "action": "toggle", "key": "electricity"})
    assert con.execute("SELECT active FROM metrics WHERE key='electricity'").fetchone()[0] == 0
    assert adm.post("/admin/metrics", data={"action": "toggle", "key": "electricity"}).status_code == 400
