import sqlite3

import pytest

from safety.db import connect
from safety.perms import PERMISSIONS, role_permissions
from safety.schema import NOTIFY_EVENTS, ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_user

NEW_EVENTS = {"capa_assigned", "capa_done", "capa_verified", "capa_reopened", "capa_due_soon", "capa_overdue", "patrol_due", "patrol_overdue"}


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.ch = add_chain(db, "P")
        self.u = add_user(db, "assessor", "ผู้ตรวจ")
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','แบบตรวจ 1')").lastrowid
        self.item = db.execute("INSERT INTO checklist_items(checklist_id, section, text_th) VALUES(?,?,?)", (self.cl, "หมวด", "ข้อ 1")).lastrowid
        db.commit()

    def plan(self, **kw):
        cols = {"checklist_id": self.cl, "risk_area_id": self.ch["a"], "frequency": "weekly", **kw}
        cur = self.db.execute(f"INSERT INTO patrol_plans({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
        self.db.commit()
        return cur.lastrowid

    def round(self, status="draft", code="PT-2603-0001"):
        cur = self.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status) VALUES(?,?,?,?,?)",
                              (code, self.cl, self.ch["a"], self.u, status))
        rid = cur.lastrowid
        aid = self.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th) VALUES(?,?,?,?)", (rid, self.item, "หมวด", "ข้อ 1")).lastrowid
        self.db.commit()
        return rid, aid

    def capa(self, **kw):
        cols = {"code": "CP-2603-0001", "source_kind": "manual", "action_type": "corrective", "title": "t", "risk_area_id": self.ch["a"],
                "owner_id": self.u, "due_date": "2026-04-01", "created_by": self.u, **kw}
        cur = self.db.execute(f"INSERT INTO capas({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
        self.db.commit()
        return cur.lastrowid


@pytest.fixture
def w(db):
    return W(db)


def test_schema_creates_all_tables_idempotently(db):
    ensure_schema(db); ensure_schema(db)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"checklists", "checklist_items", "patrol_plans", "patrol_rounds", "patrol_answers", "patrol_attachments", "capas", "capa_events"} <= names
    assert "patrol_answer_id" in {r[1] for r in db.execute("PRAGMA table_info(hazards)")}


def test_new_events_rules_and_settings_seeded_once(db):
    assert NEW_EVENTS <= set(NOTIFY_EVENTS)
    rows = db.execute("SELECT event, recipients, offset_days FROM notify_rules WHERE event IN (%s) ORDER BY id" % ",".join("?" * len(NEW_EVENTS)), tuple(NEW_EVENTS)).fetchall()
    by = {}
    for r in rows:
        by.setdefault(r["event"], []).append((r["recipients"], r["offset_days"]))
    assert by["capa_assigned"] == [("owner", 0)] and by["capa_done"] == [("role:assessor", 0)]
    assert by["capa_verified"] == [("owner", 0)] and by["capa_reopened"] == [("owner", 0)] and by["capa_due_soon"] == [("owner", 2)]
    assert by["capa_overdue"] == [("owner", 1), ("owner,role:dept_head", 3), ("role:admin,role:dept_head", 7)]
    assert by["patrol_due"] == [("owner", 0)] and by["patrol_overdue"] == [("owner,role:dept_head", 1), ("owner,role:dept_head", 5)]
    assert db.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0] == 26
    assert db.execute("SELECT value FROM app_settings WHERE key='require_capa_verified'").fetchone()[0] == "0"
    db.execute("DELETE FROM notify_rules WHERE event LIKE 'capa_%' OR event LIKE 'patrol_%'")
    db.execute("UPDATE app_settings SET value='1' WHERE key='require_capa_verified'"); db.commit()
    ensure_schema(db)
    assert db.execute("SELECT COUNT(*) FROM notify_rules WHERE event LIKE 'capa_%' OR event LIKE 'patrol_%'").fetchone()[0] == 0
    assert db.execute("SELECT value FROM app_settings WHERE key='require_capa_verified'").fetchone()[0] == "1"       # ไม่ทับค่าที่แอดมินตั้ง


def test_rules_table_accepts_new_events_after_upgrade_from_phase6(tmp_path):
    con = connect(str(tmp_path / "old.db"))
    ensure_schema(con)
    old = [e for e in NOTIFY_EVENTS if e not in NEW_EVENTS]
    con.execute("DROP TABLE notify_rules")
    con.execute("CREATE TABLE notify_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL CHECK (event IN (%s)), recipients TEXT NOT NULL DEFAULT '', "
                "channels TEXT NOT NULL DEFAULT 'inapp', offset_days INTEGER NOT NULL DEFAULT 0, min_score INTEGER, enabled INTEGER NOT NULL DEFAULT 1)"
                % ",".join(repr(e) for e in old))
    con.execute("INSERT INTO notify_rules(id,event,recipients) VALUES(7,'hazard_closed','reporter')")
    con.execute("DELETE FROM app_settings WHERE key IN ('patrol_rules_seeded','require_capa_verified')"); con.commit()
    ensure_schema(con)
    assert con.execute("SELECT recipients FROM notify_rules WHERE id=7").fetchone()[0] == "reporter"
    con.execute("INSERT INTO notify_rules(event,recipients) VALUES('capa_overdue','owner')")


@pytest.mark.parametrize("sql", [
    "INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency) VALUES(1,1,'yearly')",
    "INSERT INTO patrol_answers(round_id, item_id, section, text_th, result) VALUES(1,1,'s','t','maybe')",
])
def test_check_constraints_plans_and_answers(w, sql):
    w.round()
    with pytest.raises(sqlite3.IntegrityError):
        w.db.execute(sql)


def test_check_constraints_rounds_and_capas(w):
    with pytest.raises(sqlite3.IntegrityError):
        w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status) VALUES('X',1,1,1,'weird')")
    for bad in ({"source_kind": "other"}, {"action_type": "x"}, {"status": "weird"}, {"effectiveness_result": "maybe"}):
        with pytest.raises(sqlite3.IntegrityError):
            w.capa(**bad)
        w.db.rollback()


def test_unique_active_plan_per_checklist_and_area_but_inactive_may_coexist(w):
    w.plan()
    with pytest.raises(sqlite3.IntegrityError):
        w.plan(frequency="daily")
    w.db.rollback()
    w.db.execute("UPDATE patrol_plans SET active=0"); w.db.commit()
    w.plan(frequency="monthly")
    assert w.db.execute("SELECT COUNT(*) FROM patrol_plans").fetchone()[0] == 2


def test_unique_checklist_code_nocase_and_one_answer_per_item_per_round(w):
    with pytest.raises(sqlite3.IntegrityError):
        w.db.execute("INSERT INTO checklists(code,name_th) VALUES('cl1','ซ้ำ')")
    w.db.rollback()
    rid, _ = w.round()
    with pytest.raises(sqlite3.IntegrityError):
        w.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th) VALUES(?,?,?,?)", (rid, w.item, "s", "t"))


def test_unique_hazard_per_patrol_answer_and_unique_capa_per_patrol_answer(w):
    rid, aid = w.round()
    rep = add_user(w.db, "reporter")
    add_hazard(w.db, w.ch["a"], rep, code="HZ-A", patrol_answer_id=aid)
    with pytest.raises(sqlite3.IntegrityError):
        add_hazard(w.db, w.ch["a"], rep, code="HZ-B", patrol_answer_id=aid)
    w.db.rollback()
    add_hazard(w.db, w.ch["a"], rep, code="HZ-C"); add_hazard(w.db, w.ch["a"], rep, code="HZ-D")                  # NULL ซ้ำได้
    w.capa(source_kind="patrol", source_id=aid)
    with pytest.raises(sqlite3.IntegrityError):
        w.capa(code="CP-2603-0002", source_kind="patrol", source_id=aid)
    w.db.rollback()
    w.capa(code="CP-2603-0003", source_kind="hazard", source_id=aid); w.capa(code="CP-2603-0004", source_kind="hazard", source_id=aid)   # ชนิดอื่นซ้ำได้


def test_submitted_round_and_children_are_immutable_but_drafts_stay_editable(w):
    rid, aid = w.round("draft")
    att = w.db.execute("INSERT INTO patrol_attachments(answer_id, stored_name, orig_name, size) VALUES(?,?,?,?)", (aid, "a.jpg", "a.jpg", 1)).lastrowid
    w.db.execute("UPDATE patrol_answers SET result='pass', note='ok' WHERE id=?", (aid,))                     # ร่างแก้ได้
    w.db.execute("UPDATE patrol_rounds SET status='submitted', passed=1, failed=0, na=0, score_pct=100, submitted_at='2026-03-01 10:00:00' WHERE id=?", (rid,))
    w.db.commit()
    for stmt, args in (("UPDATE patrol_rounds SET passed=9 WHERE id=?", (rid,)), ("UPDATE patrol_rounds SET status='draft' WHERE id=?", (rid,)),
                       ("DELETE FROM patrol_rounds WHERE id=?", (rid,)), ("UPDATE patrol_answers SET result='fail' WHERE id=?", (aid,)),
                       ("DELETE FROM patrol_answers WHERE id=?", (aid,)), ("UPDATE patrol_attachments SET size=2 WHERE id=?", (att,)),
                       ("DELETE FROM patrol_attachments WHERE id=?", (att,)),
                       ("INSERT INTO patrol_attachments(answer_id, stored_name, orig_name, size) VALUES(%d,'b.jpg','b.jpg',1)" % aid, ())):
        with pytest.raises(sqlite3.IntegrityError):
            w.db.execute(stmt, args)
    item2 = w.db.execute("INSERT INTO checklist_items(checklist_id, section, text_th) VALUES(?,?,?)", (w.cl, "ห", "ข้อ 2")).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        w.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th) VALUES(?,?,?,?)", (rid, item2, "ห", "ข้อ 2"))
    assert w.db.execute("SELECT result, note FROM patrol_answers WHERE id=?", (aid,)).fetchone()[:] == ("pass", "ok")


def test_draft_round_can_be_deleted_with_its_answers(w):
    rid, aid = w.round("draft")
    w.db.execute("DELETE FROM patrol_answers WHERE round_id=?", (rid,)); w.db.execute("DELETE FROM patrol_rounds WHERE id=?", (rid,)); w.db.commit()
    assert w.db.execute("SELECT COUNT(*) FROM patrol_rounds").fetchone()[0] == 0


def test_capa_events_append_only(w):
    cid = w.capa()
    w.db.execute("INSERT INTO capa_events(capa_id, actor_id, kind) VALUES(?,?,?)", (cid, w.u, "created")); w.db.commit()
    for stmt in ("UPDATE capa_events SET note='x'", "DELETE FROM capa_events", "INSERT OR REPLACE INTO capa_events(id, capa_id, kind) VALUES(1, 1, 'x')"):
        with pytest.raises(sqlite3.IntegrityError):
            w.db.execute(stmt)


def test_new_permission_keys_and_default_roles(db):
    keys = {"patrol.manage", "patrol.perform", "patrol.view", "capa.create", "capa.verify"}
    assert keys <= set(PERMISSIONS) and keys <= role_permissions(db, "admin")
    assert {"patrol.perform", "patrol.view", "capa.create", "capa.verify"} <= role_permissions(db, "assessor") and "patrol.manage" not in role_permissions(db, "assessor")
    assert {"patrol.view", "capa.create"} <= role_permissions(db, "dept_head") and not ({"patrol.perform", "capa.verify"} & role_permissions(db, "dept_head"))
    for role in ("reporter", "viewer"):
        assert not (keys & role_permissions(db, role))


def test_existing_phase6_database_upgrades_in_place(tmp_path):
    con = connect(str(tmp_path / "p6.db"))
    ensure_schema(con)
    for t in ("capa_events", "capas", "patrol_attachments", "patrol_answers", "patrol_rounds", "patrol_plans", "checklist_items", "checklists"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    ch = add_chain(con, "U"); rep = add_user(con, "reporter", "เดิม")
    hid = add_hazard(con, ch["a"], rep, code="HZ-OLD")
    con.commit()
    ensure_schema(con)
    assert con.execute("SELECT code FROM hazards WHERE id=?", (hid,)).fetchone()[0] == "HZ-OLD"
    assert con.execute("SELECT COUNT(*) FROM checklists").fetchone()[0] == 0 and con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
