import sqlite3

import pytest

from safety.db import connect
from safety.perms import PERMISSIONS, role_permissions
from safety.schema import ensure_schema
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_user
from tests.safety.helpers import login


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def mk_incident(db, **kw):
    ch = {"a": db.execute("SELECT id FROM risk_areas LIMIT 1").fetchone()} if db.execute("SELECT 1 FROM risk_areas").fetchone() else add_chain(db, "I")
    ch = {"a": ch["a"][0] if not isinstance(ch["a"], int) else ch["a"]}
    u = add_user(db, "reporter", "ผู้แจ้ง")
    cols = {"code": "IN-2603-0001", "kind": "accident", "occurred_at": "2026-03-01 09:00", "risk_area_id": ch["a"], "title": "t",
            "reported_by": u, **kw}
    cur = db.execute(f"INSERT INTO incidents({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
    db.commit()
    return cur.lastrowid, u


def test_ensure_schema_creates_incident_tables_idempotently(db):
    ensure_schema(db)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"incidents", "incident_people", "incident_events", "incident_attachments", "injury_severities", "incident_causes"} <= names
    assert "incident_id" in {r[1] for r in db.execute("PRAGMA table_info(notifications)")}


def test_injury_severities_seeded_once_and_admin_changes_survive(db):
    rows = db.execute("SELECT code, rank, counts_lost_time FROM injury_severities ORDER BY rank").fetchall()
    assert [r["code"] for r in rows] == ["property_only", "first_aid", "medical", "lost_time", "permanent", "fatality"]
    assert {r["code"] for r in rows if r["counts_lost_time"]} == {"lost_time", "permanent", "fatality"}
    db.execute("UPDATE injury_severities SET name_th='ชื่อใหม่', active=0 WHERE code='first_aid'")
    db.execute("DELETE FROM injury_severities WHERE code='medical'")
    db.commit()
    ensure_schema(db)
    assert db.execute("SELECT COUNT(*) FROM injury_severities").fetchone()[0] == 5
    assert db.execute("SELECT name_th, active FROM injury_severities WHERE code='first_aid'").fetchone()[:] == ("ชื่อใหม่", 0)


def test_default_incident_causes_seeded_once(db):
    assert db.execute("SELECT COUNT(*) FROM incident_causes").fetchone()[0] == 5
    db.execute("DELETE FROM incident_causes"); db.commit()
    ensure_schema(db)
    assert db.execute("SELECT COUNT(*) FROM incident_causes").fetchone()[0] == 0


def test_incident_events_append_only(db):
    iid, u = mk_incident(db)
    db.execute("INSERT INTO incident_events(incident_id, actor_id, kind) VALUES(?,?,?)", (iid, u, "reported")); db.commit()
    for stmt in ("UPDATE incident_events SET note='x'", "DELETE FROM incident_events",
                 "INSERT OR REPLACE INTO incident_events(id, incident_id, kind) VALUES(1, 1, 'x')"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(stmt)


def test_incident_check_constraints(db):
    with pytest.raises(sqlite3.IntegrityError):
        mk_incident(db, kind="oops")
    db.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        mk_incident(db, status="weird")
    db.rollback()
    iid, _ = mk_incident(db, code="IN-2603-0002")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO incident_people(incident_id, role, name) VALUES(?,?,?)", (iid, "alien", "x"))


def test_new_permission_keys_and_default_roles(db):
    for key in ("incident.report", "incident.investigate", "incident.close"):
        assert key in PERMISSIONS
    assert {"incident.report", "incident.investigate", "incident.close"} <= role_permissions(db, "assessor")
    assert {"incident.report", "incident.investigate"} <= role_permissions(db, "dept_head") and "incident.close" not in role_permissions(db, "dept_head")
    assert role_permissions(db, "reporter") >= {"incident.report"} and "incident.investigate" not in role_permissions(db, "reporter")
    assert not any(p.startswith("incident.") for p in role_permissions(db, "viewer"))
    assert {p for p in PERMISSIONS if p.startswith("incident.")} <= role_permissions(db, "admin")


def test_incident_causes_master_page_works(safety_app):
    c = login(safety_app, "admin")
    assert c.get("/master/incident-causes").status_code == 200
    r = c.post("/master/incident-causes/new", data={"_csrf": csrf(c), "code": "X1", "name_th": "สาเหตุทดสอบ"}, follow_redirects=True)
    assert "สาเหตุทดสอบ" in r.get_data(as_text=True)


def test_injury_admin_requires_permission_and_saves(safety_app):
    assert login(safety_app, "viewer").get("/admin/injury-severities").status_code == 403
    c = login(safety_app, "admin")
    page = c.get("/admin/injury-severities")
    assert page.status_code == 200 and "หยุดงาน" in page.get_data(as_text=True)
    assert c.post("/admin/injury-severities", data={"id": "1", "name_th": "x"}).status_code == 400          # CSRF
    con = connect(safety_app.config["DATABASE"])
    row = con.execute("SELECT * FROM injury_severities WHERE code='medical'").fetchone()
    tok = csrf(c)
    c.post("/admin/injury-severities", data={"_csrf": tok, "id": row["id"], "name_th": "รักษา ปรับชื่อ", "rank": "7",
                                              "counts_lost_time": "1", "active": "1"})
    new = con.execute("SELECT * FROM injury_severities WHERE id=?", (row["id"],)).fetchone()
    assert (new["name_th"], new["rank"], new["counts_lost_time"], new["active"]) == ("รักษา ปรับชื่อ", 7, 1, 1)
    c.post("/admin/injury-severities", data={"_csrf": tok, "id": row["id"], "name_th": "ก" * 201, "rank": "1"})
    assert con.execute("SELECT name_th FROM injury_severities WHERE id=?", (row["id"],)).fetchone()[0] == "รักษา ปรับชื่อ"
    c.post("/admin/injury-severities", data={"_csrf": tok, "id": row["id"], "name_th": "ปิด", "rank": "1"})       # ไม่ส่ง active = ปิดใช้งาน
    assert con.execute("SELECT active FROM injury_severities WHERE id=?", (row["id"],)).fetchone()[0] == 0
    assert c.post("/admin/injury-severities", data={"_csrf": tok, "action": "delete", "id": row["id"]}).status_code in (200, 302, 400, 404)
    assert con.execute("SELECT COUNT(*) FROM injury_severities").fetchone()[0] == 6                           # ไม่มีการลบ


def test_existing_phase5_database_upgrades_in_place(tmp_path):
    con = connect(str(tmp_path / "old.db"))
    ensure_schema(con)
    for t in ("incident_events", "incident_attachments", "incident_people", "incidents", "injury_severities", "incident_causes"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.execute("DELETE FROM app_settings WHERE key IN ('injury_severities_seeded','incident_causes_seeded')")
    ch = add_chain(con, "O")
    u = add_user(con, "reporter", "เก่า")
    con.commit()
    ensure_schema(con)
    assert con.execute("SELECT COUNT(*) FROM injury_severities").fetchone()[0] == 6
    assert con.execute("SELECT name FROM safety_users WHERE id=?", (u,)).fetchone()[0] == "เก่า" and ch["a"]
