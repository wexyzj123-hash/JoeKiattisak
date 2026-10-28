import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta

import pytest

from safety import notify, scheduler, timeutil
from safety.db import connect
from safety.scheduler import backup_database, due_run_needed, run_daily, start_scheduler
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_user

D = date(2026, 3, 20)                                      # วันครบกำหนดตัวอย่าง


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class S:
    def __init__(self, db):
        self.db = db
        self.ch = add_chain(db)
        self.rep = add_user(db, "reporter", "ผู้แจ้ง")
        self.owner = add_user(db, "assessor", "เจ้าของ")
        self.head = add_user(db, "dept_head", "หัวหน้า")
        self.admin = add_user(db, "admin", "แอดมิน")

    def hz(self, due=D, status="assigned", **kw):
        return add_hazard(self.db, self.ch["a"], self.rep, status=status, due_date=due.isoformat(), owner_id=self.owner,
                          score=9, level_name="ปานกลาง", level_color="#f1c40f", **kw)

    def who(self, event=None):
        q = "SELECT u.name FROM notifications n JOIN safety_users u ON u.id = n.user_id"
        return sorted(r[0] for r in self.db.execute(q + (" WHERE n.event=?" if event else ""), (event,) if event else ()))


@pytest.fixture
def s(db):
    return S(db)


def run(db, day, **kw):
    return run_daily(db, day, force=kw.pop("force", True), backup_dir=kw.pop("backup_dir", None), **kw)


def test_run_daily_twice_same_day_skips_second(s):
    s.hz()
    first = run_daily(s.db, D - timedelta(days=2))
    assert first["skipped"] is False and first["notifications"] == 1
    assert run_daily(s.db, D - timedelta(days=2)) == {"skipped": True}
    assert s.db.execute("SELECT run_date, finished_at IS NOT NULL FROM job_runs").fetchall()[0][:] == ((D - timedelta(days=2)).isoformat(), 1)


def test_force_reruns_but_notifications_still_deduped(s):
    s.hz()
    day = D - timedelta(days=2)
    assert run_daily(s.db, day)["notifications"] == 1
    again = run_daily(s.db, day, force=True)
    assert again["skipped"] is False and again["notifications"] == 0 and len(s.who()) == 1


@pytest.mark.parametrize("delta, expected", [(-5, 0), (-3, 0), (-2, 1), (-1, 0), (0, 0)])
def test_due_soon_fires_exactly_on_offset_day(s, delta, expected):
    s.hz()
    assert run(s.db, D + timedelta(days=delta))["notifications"] == expected
    assert s.who("due_soon") == (["เจ้าของ"] if expected else [])


def test_overdue_levels_escalate_on_day_1_3_7_with_wider_recipients(s):
    s.hz()
    run(s.db, D + timedelta(days=1))
    assert s.who("overdue") == ["เจ้าของ"]
    run(s.db, D + timedelta(days=2))
    assert s.who("overdue") == ["เจ้าของ"]                                                   # วันที่ 2 ไม่มีกฎ
    run(s.db, D + timedelta(days=3))
    assert s.who("overdue") == ["หัวหน้า", "เจ้าของ", "เจ้าของ"]                              # เจ้าของ (ระดับ 2) + หัวหน้า
    run(s.db, D + timedelta(days=7))
    assert s.who("overdue").count("แอดมิน") == 1 and s.who("overdue").count("หัวหน้า") == 2


def test_closed_and_rejected_hazards_never_remind(s):
    s.hz(status="closed"); s.hz(status="rejected")
    live = s.hz()
    for delta in (-2, 1, 3, 7):
        run(s.db, D + timedelta(days=delta))
    assert {r[0] for r in s.db.execute("SELECT hazard_id FROM notifications")} == {live}


def test_due_today_is_not_overdue(s):
    s.hz()
    assert run(s.db, D)["notifications"] == 0 and s.who("overdue") == []


def test_hazard_without_due_date_ignored(s):
    add_hazard(s.db, s.ch["a"], s.rep, status="assigned", owner_id=s.owner)
    assert run(s.db, D)["notifications"] == 0


def test_one_failing_hazard_does_not_abort_run(s, monkeypatch):
    bad, good1, good2 = s.hz(), s.hz(), s.hz()
    real = notify.emit

    def flaky(db, event, hazard_id, actor_id, **kw):
        if hazard_id == bad:
            raise RuntimeError("boom")
        return real(db, event, hazard_id, actor_id, **kw)

    monkeypatch.setattr(notify, "emit", flaky)
    res = run(s.db, D - timedelta(days=2))
    assert res["errors"] == 1 and res["notifications"] == 2
    assert {r[0] for r in s.db.execute("SELECT hazard_id FROM notifications")} == {good1, good2}


def test_missed_days_do_not_backfill_old_stages(s):
    s.hz()
    # ระบบไม่ได้ทำงานหลายวัน: วันนี้เกินมา 5 วัน ซึ่งไม่มีกฎ offset 5 → ไม่ส่งขั้น 1/3 ย้อนหลัง
    assert run(s.db, D + timedelta(days=5))["notifications"] == 0 and s.who() == []
    assert run(s.db, D + timedelta(days=3))["notifications"] == 2                              # (ควบคุม) วันที่ 3 มีกฎจริง → เจ้าของ + หัวหน้า


def test_due_run_needed_catch_up_and_before_time(s):
    day = D
    assert due_run_needed(s.db, datetime(2026, 3, 20, 7, 29)) is False                         # ยังไม่ถึง 07:30
    assert due_run_needed(s.db, datetime(2026, 3, 20, 7, 30)) is True
    assert due_run_needed(s.db, datetime(2026, 3, 20, 18, 0)) is True                          # เปิดเครื่องสายก็ตามทัน
    run_daily(s.db, day)
    assert due_run_needed(s.db, datetime(2026, 3, 20, 18, 0)) is False
    assert due_run_needed(s.db, datetime(2026, 3, 21, 7, 30)) is True                          # วันใหม่
    s.db.execute("UPDATE app_settings SET value='garbage' WHERE key='daily_time'"); s.db.commit()
    assert due_run_needed(s.db, datetime(2026, 3, 22, 7, 30)) is True                          # ค่าผิด → ใช้ 07:30


def test_backup_creates_valid_database_and_prunes_to_14(s, tmp_path):
    backups = tmp_path / "bk"
    backups.mkdir()
    for i in range(20):
        (backups / f"safety-202601{i + 1:02d}.db").write_bytes(b"old")
    (backups / "notes.txt").write_text("keep me")
    path = backup_database(str(tmp_path / "s.db"), str(backups), date(2026, 3, 20), keep=14)
    assert os.path.basename(path) == "safety-20260320.db"
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM notify_rules").fetchone()[0] == 9 and con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    assert sorted(os.listdir(backups)).count("safety-20260320.db-wal") == 0                     # ไม่มีไฟล์ข้างเคียง
    files = sorted(f for f in os.listdir(backups) if f.startswith("safety-") and f.endswith(".db"))
    assert len(files) == 14 and files[-1] == "safety-20260320.db" and "safety-20260101.db" not in files
    assert (backups / "notes.txt").exists() and not [f for f in os.listdir(backups) if f.endswith(".tmp")]


def test_backup_atomic_no_partial_file_on_failure(s, tmp_path, monkeypatch):
    backups = tmp_path / "bk"
    real_connect = sqlite3.connect

    class Boom:
        def __init__(self, *a, **k): self._c = real_connect(*a, **k)
        def backup(self, *a, **k): raise sqlite3.OperationalError("disk full")
        def close(self): self._c.close()
        def __getattr__(self, n): return getattr(self._c, n)

    monkeypatch.setattr(scheduler.sqlite3, "connect", lambda *a, **k: Boom(*a, **k))
    with pytest.raises(sqlite3.OperationalError):
        backup_database(str(tmp_path / "s.db"), str(backups), date(2026, 3, 20))
    assert not backups.exists() or os.listdir(backups) == []


def test_run_daily_makes_a_backup_and_survives_backup_failure(s, tmp_path, monkeypatch):
    res = run_daily(s.db, D, backup_dir=str(tmp_path / "b1"))
    assert res["backup"] and os.path.isfile(res["backup"])
    monkeypatch.setattr(scheduler, "backup_database", lambda *a, **k: (_ for _ in ()).throw(OSError("no space")))
    res2 = run_daily(s.db, D + timedelta(days=1), backup_dir=str(tmp_path / "b2"))
    assert res2["backup"] is None and res2["skipped"] is False


def test_concurrent_run_daily_only_one_wins(tmp_path):
    path = str(tmp_path / "c.db")
    con = connect(path); ensure_schema(con); con.close()
    results, barrier = [], threading.Barrier(4)

    def worker():
        c = connect(path)
        barrier.wait()
        results.append(run_daily(c, D, backup_dir=str(tmp_path / "b")))
        c.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]; [t.join(10) for t in threads]
    assert sorted(r.get("skipped", False) for r in results) == [False, True, True, True]


def test_scheduler_thread_runs_catch_up_once_and_stops(tmp_path, monkeypatch):
    from safety.app import create_app
    from tests.conftest import SECRET
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"), "SSO_SECRET": SECRET, "SERVICE_TOKEN": "x", "HUB_URL": "http://h"})
    con = connect(app.config["DATABASE"]); con.execute("UPDATE app_settings SET value='00:00' WHERE key='daily_time'"); con.commit(); con.close()
    calls = []
    monkeypatch.setattr(scheduler, "run_daily", lambda db, *a, **k: (calls.append(1), db.execute("INSERT OR IGNORE INTO job_runs(job, run_date) VALUES('daily', ?)", (timeutil.today().isoformat(),)), db.commit(), {})[-1])
    th = start_scheduler(app, interval=0.02)
    deadline = time.time() + 3
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.15)
    th.stop_event.set(); th.join(2)
    assert len(calls) == 1 and th.daemon and not th.is_alive()
