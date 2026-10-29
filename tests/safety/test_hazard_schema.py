import sqlite3
from datetime import date

import pytest

from safety.db import connect
from safety.hazards import add_event, can_view_all, hazard_scope_sql, next_code, risk_area_options, visible_hazard
from safety.perms import user_permissions
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_user, user_row


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def test_hazard_tables_created_and_status_check(db):
    for t in ("hazards", "hazard_attachments", "hazard_events"):
        db.execute(f"SELECT * FROM {t}")
    ch = add_chain(db)
    u = add_user(db)
    with pytest.raises(sqlite3.IntegrityError):
        add_hazard(db, ch["a"], u, status="bogus")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO hazard_attachments(hazard_id,kind,stored_name,orig_name,size,uploaded_by) "
                   "VALUES(1,'during','x','x',1,1)")


def test_events_are_append_only(db):
    ch, u = add_chain(db), add_user(db)
    hid = add_hazard(db, ch["a"], u)
    add_event(db, hid, u, "reported", None, "reported")
    db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE hazard_events SET note='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM hazard_events")
    assert db.execute("SELECT COUNT(*) FROM hazard_events").fetchone()[0] == 1


def test_next_code_format_and_sequence(db):
    ch, u = add_chain(db), add_user(db)
    assert next_code(db, date(2026, 1, 5)) == "HZ-2601-0001"
    add_hazard(db, ch["a"], u, code="HZ-2601-0001")
    assert next_code(db, date(2026, 1, 31)) == "HZ-2601-0002"


def test_next_code_restarts_next_month(db):
    ch, u = add_chain(db), add_user(db)
    add_hazard(db, ch["a"], u, code="HZ-2601-0041")
    assert next_code(db, date(2026, 1, 31)) == "HZ-2601-0042"
    assert next_code(db, date(2026, 2, 1)) == "HZ-2602-0001"
    assert next_code(db, date(2027, 1, 1)) == "HZ-2701-0001"


def test_next_code_ignores_other_prefixes_and_survives_gaps(db):
    ch, u = add_chain(db), add_user(db)
    add_hazard(db, ch["a"], u, code="HZ-2601-0007")
    add_hazard(db, ch["a"], u, code="HZ-2512-0099")
    add_hazard(db, ch["a"], u, code="OTHER-1")
    assert next_code(db, date(2026, 1, 9)) == "HZ-2601-0008"


def test_code_unique_in_database(db):
    ch, u = add_chain(db), add_user(db)
    add_hazard(db, ch["a"], u, code="HZ-2601-0001")
    with pytest.raises(sqlite3.IntegrityError):
        add_hazard(db, ch["a"], u, code="HZ-2601-0001")


def test_visible_hazard_scope_and_reporter_rules(db):
    a, b = add_chain(db, "A"), add_chain(db, "B")
    rep1, rep2 = add_user(db, "reporter", "ผู้แจ้ง 1"), add_user(db, "reporter", "ผู้แจ้ง 2")
    scoped_assessor = add_user(db, "assessor", factory_id=a["f"])
    dept_assessor = add_user(db, "assessor", department_id=b["d"])
    admin = add_user(db, "admin")
    ha, hb = add_hazard(db, a["a"], rep1), add_hazard(db, b["a"], rep2)
    seen = lambda uid, hid: visible_hazard(db, user_row(db, uid), hid) is not None
    assert seen(admin, ha) and seen(admin, hb)                                    # แอดมินไม่จำกัดขอบเขต เห็นทั้งหมด
    assert seen(scoped_assessor, ha) and not seen(scoped_assessor, hb)            # ขอบเขตโรงงาน
    assert seen(dept_assessor, hb) and not seen(dept_assessor, ha)                # ขอบเขตแผนก
    assert seen(rep1, ha) and not seen(rep1, hb) and not seen(rep2, ha)           # ผู้แจ้งเห็นเฉพาะของตัวเอง
    assert visible_hazard(db, user_row(db, admin), 9999) is None


def test_reporter_in_other_factory_scope_cannot_see_even_own_hazard(db):
    a, b = add_chain(db, "A"), add_chain(db, "B")
    rep = add_user(db, "reporter", factory_id=b["f"])
    hid = add_hazard(db, a["a"], rep)
    assert visible_hazard(db, user_row(db, rep), hid) is None


def test_can_view_all_by_permission(db):
    assert can_view_all({"hazard.assess"}) and can_view_all({"master.view"}) and can_view_all({"hazard.close"})
    assert not can_view_all({"hazard.report"}) and not can_view_all(set())
    assert can_view_all(user_permissions(db, {"role": "viewer"})) and not can_view_all(user_permissions(db, {"role": "reporter"}))


def test_hazard_scope_sql_unscoped_is_true(db):
    assert hazard_scope_sql(user_row(db, add_user(db))) == ("1=1", [])


def test_risk_area_options_require_active_ancestors_and_scope(db):
    a, b = add_chain(db, "A"), add_chain(db, "B")
    admin = user_row(db, add_user(db))
    assert [o["code"] for o in risk_area_options(db, admin)] == ["RA", "RB"]
    assert risk_area_options(db, admin)[0]["breadcrumb"] == "โรงงาน A › แผนก A › ไลน์ A › พื้นที่ A"
    for table, key in (("factories", "f"), ("departments", "d"), ("lines", "l"), ("risk_areas", "a")):
        db.execute(f"UPDATE {table} SET active=0 WHERE id=?", (a[key],))
        db.commit()
        assert [o["code"] for o in risk_area_options(db, admin)] == ["RB"], table
        db.execute(f"UPDATE {table} SET active=1 WHERE id=?", (a[key],))
        db.commit()
    scoped = user_row(db, add_user(db, "reporter", factory_id=b["f"]))
    assert [o["code"] for o in risk_area_options(db, scoped)] == ["RB"]


def test_add_event_roundtrip_with_json_data(db):
    ch, u = add_chain(db), add_user(db)
    hid = add_hazard(db, ch["a"], u)
    add_event(db, hid, u, "assessed", "reported", "assessed", note="ประเมินแล้ว", data={"old": [1, 2], "ชื่อ": "ก"})
    db.commit()
    ev = db.execute("SELECT * FROM hazard_events").fetchone()
    assert (ev["kind"], ev["from_status"], ev["to_status"], ev["note"], ev["actor_id"]) == \
        ("assessed", "reported", "assessed", "ประเมินแล้ว", u)
    import json
    assert json.loads(ev["data"]) == {"old": [1, 2], "ชื่อ": "ก"} and ev["at"]
