from datetime import date, datetime

import pytest

from safety import notify, report_due as rd, scheduler, timeutil
from safety.db import connect
from safety.schema import NOTIFY_EVENTS, ensure_schema
from tests.safety.factories import add_chain, add_user, user_row

TODAY = date(2026, 3, 20)


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: datetime(2026, 3, 20, 10, 0))
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        u = lambda role, name, **kw: add_user(db, role, name, **kw)
        self.head_a = u("dept_head", "หัวหน้า A", department_id=self.a["d"])
        self.head_b = u("dept_head", "หัวหน้า B", department_id=self.b["d"])
        self.asr = u("assessor", "ผู้ประเมินกลาง")
        self.admin = u("admin", "แอดมิน")
        self.reporter = u("reporter", "ผู้แจ้ง")

    def rule(self, dept=None, key="monthly", day=5, created="2026-02-10 00:00"):
        cur = self.db.execute("INSERT INTO report_due(report_key, department_id, frequency, due_day, created_at) VALUES(?,?,?,?,?)", (key, dept, "monthly", day, created))
        self.db.commit()
        return cur.lastrowid

    def notes(self, event=None):
        q = "SELECT n.*, u.name FROM notifications n JOIN safety_users u ON u.id = n.user_id WHERE n.event LIKE 'report_%'" + (" AND n.event=?" if event else "") + " ORDER BY n.id"
        return self.db.execute(q, (event,) if event else ()).fetchall()


@pytest.fixture
def w(db):
    return W(db)


def test_events_and_default_rules_seeded_once(db):
    assert {"report_due_soon", "report_overdue"} <= set(NOTIFY_EVENTS)
    rows = db.execute("SELECT event, recipients, offset_days FROM notify_rules WHERE event LIKE 'report_%' ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("report_due_soon", "role:dept_head", 3), ("report_overdue", "role:dept_head", 1), ("report_overdue", "role:assessor", 3)]
    db.execute("DELETE FROM notify_rules WHERE event LIKE 'report_%'"); db.commit()
    ensure_schema(db)
    assert db.execute("SELECT COUNT(*) FROM notify_rules WHERE event LIKE 'report_%'").fetchone()[0] == 0          # แอดมินลบแล้วไม่ถูกใส่กลับ
    assert db.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0] == 23


def test_upgrade_from_older_rule_table_adds_report_events_keeping_edits(tmp_path):
    con = connect(str(tmp_path / "old.db"))
    ensure_schema(con)
    con.execute("UPDATE notify_rules SET channels='inapp' WHERE event='capa_due_soon'")
    con.execute("DELETE FROM app_settings WHERE key='report_rules_seeded'")
    con.execute("DELETE FROM notify_rules WHERE event LIKE 'report_%'")
    con.commit()
    ensure_schema(con)
    assert con.execute("SELECT channels FROM notify_rules WHERE event='capa_due_soon'").fetchone()[0] == "inapp"
    assert con.execute("SELECT COUNT(*) FROM notify_rules WHERE event LIKE 'report_%'").fetchone()[0] == 3


def test_due_soon_and_overdue_emitted_once_with_catch_up(w):
    w.rule(dept=w.a["d"], day=22, created="2026-03-01 00:00")                                  # งวด 2026-02 ครบ 22 มี.ค. → อีก 2 วัน
    r1 = scheduler.run_daily(w.db, today=TODAY, force=True)
    assert r1["report_notifications"] == 1 and [n["name"] for n in w.notes("report_due_soon")] == ["หัวหน้า A"]
    assert scheduler.run_daily(w.db, today=TODAY, force=True)["report_notifications"] == 0                          # รันซ้ำไม่ซ้ำ
    later = date(2026, 3, 26)                                       # เลยกำหนด 4 วัน (ข้ามวันที่ครบ 1 วันไป) → ตามทันขั้น 1 และ 3
    r2 = scheduler.run_daily(w.db, today=later, force=True)
    assert r2["report_notifications"] == 2 and {n["name"] for n in w.notes("report_overdue")} == {"หัวหน้า A", "ผู้ประเมินกลาง"}


def test_submitted_period_sends_nothing(w):
    w.rule(dept=w.a["d"], day=5)
    assert rd.mark_submitted(w.db, user_row(w.db, w.admin), "monthly", w.a["d"], "2026-02", "") == []
    assert scheduler.run_daily(w.db, today=TODAY, force=True)["report_notifications"] == 0 and w.notes() == []


def test_reminder_goes_only_to_users_in_department_scope(w):
    w.rule(dept=w.b["d"], day=5)
    scheduler.run_daily(w.db, today=TODAY, force=True)
    names = {n["name"] for n in w.notes()}
    assert "หัวหน้า B" in names and "หัวหน้า A" not in names and "ผู้แจ้ง" not in names
    w.rule(key="annual", day=5)                                     # ทั้งบริษัท: เฉพาะผู้ไม่มีขอบเขต
    w.db.execute("UPDATE report_due SET frequency='monthly' WHERE report_key='annual'")
    scheduler.run_daily(w.db, today=date(2026, 3, 27), force=True)
    company = {n["name"] for n in w.db.execute("SELECT n.*, u.name FROM notifications n JOIN safety_users u ON u.id=n.user_id WHERE n.link LIKE '%annual%'")}
    assert "หัวหน้า A" not in company and "หัวหน้า B" not in company and "ผู้ประเมินกลาง" in company


def test_message_has_no_free_text_or_names(w):
    rid = w.rule(dept=w.a["d"], day=5)
    scheduler.run_daily(w.db, today=TODAY, force=True)
    n = w.notes("report_overdue")[0]
    assert n["title"] == "เกินกำหนดส่งรายงาน 15 วัน: รายงานประจำเดือนตามแผนก" and "งวด 2026-02" in n["body"] and "แผนก A" in n["body"] and n["link"].startswith("/reports/monthly")


def test_run_daily_error_isolation_for_report_rules(w, monkeypatch):
    bad, ok = w.rule(dept=w.a["d"], day=5), w.rule(dept=w.b["d"], key="incidents", day=5)
    real = notify.emit_report
    def flaky(db, event, rule_id, *a, **k):
        if rule_id == bad:
            raise RuntimeError("boom")
        return real(db, event, rule_id, *a, **k)
    monkeypatch.setattr(notify, "emit_report", flaky)
    r = scheduler.run_daily(w.db, today=TODAY, force=True)
    assert r["errors"] >= 1 and r["report_notifications"] >= 1
