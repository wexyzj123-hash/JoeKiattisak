import sqlite3
import threading
from datetime import date, datetime

import pytest

from safety import report_due as rd, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_user, user_row

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
        self.head = u("dept_head", "หัวหน้า A", department_id=self.a["d"])
        self.rep = u("reporter", "ผู้แจ้ง")

    def rule(self, key="monthly", dept=None, freq="monthly", day=5, month=None, created="2026-02-10 00:00"):
        cur = self.db.execute("INSERT INTO report_due(report_key, department_id, frequency, due_day, due_month, created_at) VALUES(?,?,?,?,?,?)",
                              (key, dept, freq, day, month, created))
        self.db.commit()
        return self.db.execute("SELECT * FROM report_due WHERE id=?", (cur.lastrowid,)).fetchone()


@pytest.fixture
def w(db):
    return W(db)


def test_period_due_month_end_and_year_boundary(w):
    r = w.rule(day=28)
    assert rd.period_due(r, date(2026, 3, 1)) == ("2026-02", date(2026, 3, 28))
    assert rd.period_due(r, date(2026, 3, 29)) == ("2026-03", date(2026, 4, 28))                           # เลยกำหนดเดือนนี้ → งวดถัดไป
    assert rd.period_due(w.rule(key="incidents", day=5), date(2026, 1, 3)) == ("2025-12", date(2026, 1, 5))                  # ธ.ค. → ม.ค.
    y = w.rule(key="annual", freq="yearly", day=28, month=2)
    assert rd.period_due(y, date(2026, 1, 10)) == ("2025", date(2026, 2, 28))
    assert rd.period_due(y, date(2026, 3, 1)) == ("2026", date(2027, 2, 28))


def test_status_overdue_due_upcoming_and_submitted_overrides(w):
    r = w.rule(day=5)
    st = rd.status(w.db, r, date(2026, 3, 20))
    assert (st["state"], st["period"], st["due"], st["days"]) == ("overdue", "2026-02", date(2026, 3, 5), 15)
    assert rd.status(w.db, r, date(2026, 3, 5))["state"] == "due"
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026-02", "") == []
    st = rd.status(w.db, r, date(2026, 3, 20))
    assert (st["state"], st["period"], st["days"]) == ("upcoming", "2026-03", 16)
    new = w.rule(key="incidents", day=10, created="2026-03-19 00:00")                                         # สร้างหลังกำหนดแล้ว: ไม่เตือนย้อนหลัง
    assert rd.status(w.db, new, date(2026, 3, 20))["state"] == "upcoming"


def test_mark_submitted_once_and_append_only(w):
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026-02", "ส่งแล้ว") == []
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026-02", "") == ["ส่งรายงานงวดนี้แล้ว"]
    assert w.db.execute("SELECT COUNT(*) FROM report_submissions").fetchone()[0] == 1
    for q in ("UPDATE report_submissions SET note='x'", "DELETE FROM report_submissions"):
        with pytest.raises(sqlite3.IntegrityError):
            w.db.execute(q)
        w.db.rollback()


def test_mark_submitted_future_and_bad_period_refused(w):
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026-04", "") == ["ยังไม่ถึงงวดรายงานนี้"]
    assert rd.mark_submitted(w.db, w.admin, "annual", None, "2027", "") == ["ยังไม่ถึงงวดรายงานนี้"]
    for bad in ("2026-13", "26-01", "abc", "2026-3"):
        assert rd.mark_submitted(w.db, w.admin, "monthly", None, bad, "")
    assert rd.mark_submitted(w.db, w.admin, "nope", None, "2026-02", "")
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026-02", "ก" * 501)


def test_submission_requires_scope_and_permission(w):
    assert rd.mark_submitted(w.db, w.rep, "monthly", w.a["d"], "2026-02", "") == rd.DENIED
    assert rd.mark_submitted(w.db, w.mgr_a, "monthly", w.b["d"], "2026-02", "") == rd.NOT_FOUND
    assert rd.mark_submitted(w.db, w.mgr_a, "monthly", None, "2026-02", "") == ["ผู้ที่มีขอบเขตเฉพาะโรงงาน/แผนกต้องเลือกแผนกของตน"]
    assert rd.mark_submitted(w.db, w.mgr_a, "monthly", w.a["d"], "2026-02", "") == []
    assert rd.mark_submitted(w.db, w.mgr_a, "monthly", w.a["d"], "2026-02", "") == ["ส่งรายงานงวดนี้แล้ว"]


def test_rules_scoped_to_factory_idor(w):
    rb, ra, rall = w.rule(dept=w.b["d"]), w.rule(dept=w.a["d"], key="incidents"), w.rule(key="capa")
    assert [r["id"] for r in rd.rules(w.db, w.mgr_a)] == [ra["id"]]
    assert {r["id"] for r in rd.rules(w.db, w.admin)} == {rb["id"], ra["id"], rall["id"]}
    assert rd.toggle_rule(w.db, w.mgr_a, rb["id"]) == rd.NOT_FOUND and rd.toggle_rule(w.db, w.mgr_a, rall["id"]) == rd.NOT_FOUND
    assert rd.save_rule(w.db, w.mgr_a, {"id": str(rb["id"]), "report_key": "monthly", "frequency": "monthly", "due_day": "7"}) == rd.NOT_FOUND
    assert w.db.execute("SELECT active, due_day FROM report_due WHERE id=?", (rb["id"],)).fetchone()[:] == (1, 5)
    assert rd.toggle_rule(w.db, w.mgr_a, ra["id"]) == [] and rd.toggle_rule(w.db, w.rep, ra["id"]) == rd.DENIED


def test_save_rule_validation_and_unique_active(w):
    ok = {"report_key": "monthly", "department_id": str(w.a["d"]), "frequency": "monthly", "due_day": "5"}
    assert rd.save_rule(w.db, w.admin, ok) == []
    assert rd.save_rule(w.db, w.admin, ok) == ["มีกำหนดส่งของรายงานนี้ในแผนกนี้ที่ใช้งานอยู่แล้ว"]
    for bad in ({"due_day": "0"}, {"due_day": "29"}, {"due_day": "x"}, {"frequency": "weekly"}, {"report_key": "zzz"}, {"frequency": "yearly", "due_month": ""}):
        assert rd.save_rule(w.db, w.admin, {**ok, "report_key": "annual", **bad}), bad
    assert rd.save_rule(w.db, w.admin, {"report_key": "annual", "frequency": "yearly", "due_day": "28", "due_month": "2"}) == []
    assert rd.save_rule(w.db, w.admin, {**ok, "department_id": "9999", "report_key": "capa"}) == rd.NOT_FOUND


def test_concurrent_double_submit_single_row(w, tmp_path):
    path = str(tmp_path / "s.db")
    results, barrier = [], threading.Barrier(2)

    def go():
        con = connect(path)
        barrier.wait()
        results.append(rd.mark_submitted(con, user_row(con, w.admin["id"]), "capa", None, "2026-02", "x"))
        con.close()
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(len(r) for r in results) == [0, 1]
    assert w.db.execute("SELECT COUNT(*) FROM report_submissions").fetchone()[0] == 1


def test_only_fully_elapsed_periods_and_matching_format_accepted(w):
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026-03", "") == ["ยังไม่ถึงงวดรายงานนี้"]        # เดือนปัจจุบันยังไม่จบ
    assert rd.mark_submitted(w.db, w.admin, "annual", None, "2026", "") == ["ยังไม่ถึงงวดรายงานนี้"]
    assert rd.mark_submitted(w.db, w.admin, "monthly", None, "2026", "") == ["งวดรายงานไม่ถูกต้อง"]            # รายเดือนต้อง YYYY-MM
    assert rd.mark_submitted(w.db, w.admin, "annual", None, "2026-02", "") == ["งวดรายงานไม่ถูกต้อง"]            # รายปีต้อง YYYY
    assert rd.mark_submitted(w.db, w.admin, "capa", None, "2026-02", "") == []


def test_save_rule_frequency_must_match_report_type(w):
    assert rd.save_rule(w.db, w.admin, {"report_key": "annual", "frequency": "monthly", "due_day": "5"}) == ["รายงานนี้ใช้ความถี่รายปีเท่านั้น"]
    assert rd.save_rule(w.db, w.admin, {"report_key": "capa", "frequency": "yearly", "due_day": "5", "due_month": "2"}) == ["รายงานนี้ใช้ความถี่รายเดือนเท่านั้น"]


def test_scoped_user_can_clear_non_monthly_rule_of_own_department(w):
    r = w.rule(key="annual", dept=w.a["d"], freq="yearly", day=5, month=2)
    assert rd.mark_submitted(w.db, w.head, "annual", w.a["d"], "2025", "") == []
    assert rd.status(w.db, r, date(2026, 3, 20))["state"] == "upcoming"


def test_reenabling_rule_resets_start_so_no_retroactive_overdue(w):
    r = w.rule(day=5)
    rd.toggle_rule(w.db, w.admin, r["id"]); rd.toggle_rule(w.db, w.admin, r["id"])
    assert rd.status(w.db, w.db.execute("SELECT * FROM report_due WHERE id=?", (r["id"],)).fetchone(), date(2026, 3, 20))["state"] == "upcoming"


def test_history_for_scoped_user_not_crowded_out_by_other_scopes(w):
    w.db.execute("INSERT INTO report_submissions(report_key, department_id, period, submitted_by) VALUES('monthly',?,?,?)", (w.a["d"], "1990-01", w.admin["id"]))
    for i in range(520):
        w.db.execute("INSERT INTO report_submissions(report_key, department_id, period, submitted_by) VALUES('monthly',?,?,?)", (w.b["d"], f"{2000 + i // 12}-{i % 12 + 1:02d}", w.admin["id"]))
    w.db.commit()
    assert [s["period"] for s in rd.submissions(w.db, w.mgr_a)] == ["1990-01"]
