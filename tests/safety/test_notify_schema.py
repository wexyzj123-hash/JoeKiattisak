import sqlite3

import pytest

from safety.db import connect
from safety.schema import ensure_schema, seed_notify
from tests.safety.factories import add_chain, add_hazard, add_user

EVENTS = {"hazard_reported", "hazard_assessed", "hazard_assigned", "hazard_closed", "hazard_rejected", "due_soon", "overdue"}


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def test_tables_exist_and_checks(db):
    u = add_user(db)
    ins_rule = lambda ev: db.execute("INSERT INTO notify_rules(event, recipients, channels) VALUES(?, 'owner', 'inapp')", (ev,))
    with pytest.raises(sqlite3.IntegrityError):
        ins_rule("bogus_event")
    ob = lambda ch, st="pending": db.execute("INSERT INTO notify_outbox(channel, target, subject, body, status, next_try_at, dedupe_key) "
                                             "VALUES(?, 't', 's', 'b', ?, '2026-01-01 00:00:00', 'k')", (ch, st))
    with pytest.raises(sqlite3.IntegrityError):
        ob("sms")
    with pytest.raises(sqlite3.IntegrityError):
        ob("telegram", "weird")
    db.execute("INSERT INTO notifications(user_id, event, title, body, link, dedupe_key) VALUES(?, 'x', 't', 'b', '/', 'k')", (u,))
    for t in ("app_settings", "job_runs"):
        db.execute(f"SELECT * FROM {t}")


def test_notification_unique_per_user_dedupe_key(db):
    u1, u2 = add_user(db), add_user(db)
    ins = lambda u: db.execute("INSERT INTO notifications(user_id, event, title, body, link, dedupe_key) VALUES(?, 'e', 't', 'b', '/', 'same')", (u,))
    ins(u1); ins(u2)
    with pytest.raises(sqlite3.IntegrityError):
        ins(u1)


def test_outbox_unique_per_channel_target_key(db):
    ins = lambda ch, tgt, key: db.execute("INSERT INTO notify_outbox(channel, target, subject, body, next_try_at, dedupe_key) "
                                          "VALUES(?,?, 's','b','2026-01-01 00:00:00', ?)", (ch, tgt, key))
    ins("telegram", "1", "k"); ins("telegram", "2", "k"); ins("line", "1", "k")
    with pytest.raises(sqlite3.IntegrityError):
        ins("telegram", "1", "k")


def test_default_rules_seeded_once_and_not_overwritten(db):
    rules = db.execute("SELECT * FROM notify_rules ORDER BY id").fetchall()
    assert {r["event"] for r in rules} == EVENTS and len(rules) == 9
    by = {(r["event"], r["offset_days"]): r for r in rules}
    assert by[("hazard_reported", 0)]["recipients"] == "role:assessor" and by[("hazard_reported", 0)]["channels"] == "inapp,telegram"
    assert by[("hazard_assessed", 0)]["min_score"] == 17 and by[("hazard_assessed", 0)]["recipients"] == "role:dept_head,role:admin"
    assert by[("hazard_assigned", 0)]["channels"] == "inapp,telegram,line,email" and by[("hazard_assigned", 0)]["recipients"] == "owner"
    assert by[("hazard_closed", 0)]["recipients"] == "reporter" and by[("hazard_rejected", 0)]["recipients"] == "reporter"
    assert by[("due_soon", 2)]["recipients"] == "owner"
    assert [by[("overdue", d)]["recipients"] for d in (1, 3, 7)] == ["owner", "owner,role:dept_head", "role:admin,role:dept_head"]
    db.execute("UPDATE notify_rules SET enabled=0, channels='inapp' WHERE event='hazard_closed'")
    db.execute("DELETE FROM notify_rules WHERE event='due_soon'")
    db.commit()
    ensure_schema(db); seed_notify(db)
    assert db.execute("SELECT enabled, channels FROM notify_rules WHERE event='hazard_closed'").fetchone()[:] == (0, "inapp")
    assert db.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0] == 8                       # ไม่เติมกฎที่ลบไปกลับมา


def test_default_settings_present_and_kept(db):
    get = lambda k: db.execute("SELECT value FROM app_settings WHERE key=?", (k,)).fetchone()[0]
    assert (get("quiet_start"), get("quiet_end"), get("daily_time")) == ("22:00", "06:00", "07:30")
    db.execute("UPDATE app_settings SET value='23:15' WHERE key='quiet_start'"); db.commit()
    ensure_schema(db)
    assert get("quiet_start") == "23:15"


def test_user_contact_columns_default_inapp(db):
    u = add_user(db)
    row = db.execute("SELECT telegram_chat_id, line_user_id, email, notify_channels FROM safety_users WHERE id=?", (u,)).fetchone()
    assert tuple(row) == (None, None, None, "inapp")


def test_existing_phase3_database_upgrades_in_place(tmp_path):
    path = str(tmp_path / "old.db")
    con = connect(path)
    ensure_schema(con)
    u = add_user(con, name="เดิม")
    # จำลองฐานข้อมูลเฟส 3: ไม่มีคอลัมน์ติดต่อและไม่มีตารางแจ้งเตือน
    con.executescript("""PRAGMA foreign_keys=OFF; DROP TABLE notifications; DROP TABLE notify_rules; DROP TABLE notify_outbox;
        DROP TABLE app_settings; DROP TABLE job_runs; ALTER TABLE safety_users DROP COLUMN telegram_chat_id;
        ALTER TABLE safety_users DROP COLUMN line_user_id; ALTER TABLE safety_users DROP COLUMN email;
        ALTER TABLE safety_users DROP COLUMN notify_channels;""")
    con.close()
    con = connect(path)
    ensure_schema(con)
    row = con.execute("SELECT name, email, notify_channels FROM safety_users WHERE id=?", (u,)).fetchone()
    assert (row["name"], row["email"], row["notify_channels"]) == ("เดิม", None, "inapp")
    assert con.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0] == 9


def test_ensure_schema_idempotent(db):
    before = (db.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0], db.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0])
    ensure_schema(db); ensure_schema(db)
    assert (db.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0], db.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]) == before
