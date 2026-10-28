import io
from datetime import date, datetime, timedelta

import pytest
from PIL import Image

from safety import images, scheduler
from safety.db import connect
from safety.notify import emit_capa, emit_patrol, render_capa_message, render_patrol_message
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_user, user_row


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
        self.owner = u("reporter", "เจ้าของ CAPA")                       # ผู้แจ้งทั่วไป: ไม่มีสิทธิ์ CAPA แต่เป็นเจ้าของ
        self.creator = u("assessor", "ผู้สร้าง")
        self.asr_a = u("assessor", "ผู้ประเมิน A", factory_id=self.a["f"])
        self.asr_b = u("assessor", "ผู้ประเมิน B", factory_id=self.b["f"])
        self.head = u("dept_head", "หัวหน้า")
        self.admin = u("admin", "แอดมิน")
        self.insp = u("assessor", "ผู้ตรวจประจำ")
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','ตรวจเครื่องจักร')").lastrowid
        db.execute("INSERT INTO checklist_items(checklist_id, section, text_th) VALUES(?,?,?)", (self.cl, "ห", "ข้อ"))
        db.commit()

    def capa(self, **kw):
        n = self.db.execute("SELECT COUNT(*)+1 FROM capas").fetchone()[0]
        cols = {"code": f"CP-T-{n:04d}", "source_kind": "manual", "action_type": "corrective", "title": "ติดตั้งการ์ด", "risk_area_id": self.a["a"],
                "owner_id": self.owner, "due_date": "2026-03-20", "created_by": self.creator, **kw}
        cur = self.db.execute(f"INSERT INTO capas({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
        self.db.commit()
        return cur.lastrowid

    def plan(self, **kw):
        cols = {"checklist_id": self.cl, "risk_area_id": self.a["a"], "frequency": "weekly", "created_at": "2026-03-01 08:00:00", **kw}
        cur = self.db.execute(f"INSERT INTO patrol_plans({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
        self.db.commit()
        return cur.lastrowid

    def notes(self, event=None):
        q = "SELECT * FROM notifications" + (" WHERE event=?" if event else "") + " ORDER BY id"
        return self.db.execute(q, (event,) if event else ()).fetchall()


@pytest.fixture
def w(db):
    return W(db)


def png():
    buf = io.BytesIO(); Image.new("RGB", (30, 30), (1, 2, 3)).save(buf, "PNG"); return buf.getvalue()


def test_capa_assigned_notifies_owner_even_without_capa_permission_and_not_the_actor(w):
    cid = w.capa()
    assert emit_capa(w.db, "capa_assigned", cid, w.creator) == 1
    n = w.notes()[0]
    assert (n["user_id"], n["link"], n["event"]) == (w.owner, f"/capas/{cid}", "capa_assigned") and n["hazard_id"] is None and n["incident_id"] is None
    assert emit_capa(w.db, "capa_assigned", cid, w.creator) == 0                                   # ไม่ซ้ำ
    w.db.execute("UPDATE capas SET owner_id=? WHERE id=?", (w.creator, cid)); w.db.commit()
    assert emit_capa(w.db, "capa_assigned", cid, w.creator) == 0                                   # เจ้าของใหม่คือผู้กระทำเอง


def test_capa_done_notifies_assessors_in_scope_who_can_open_not_actor(w):
    w.db.execute("INSERT INTO safety_roles(key,name_th,permissions) VALUES('old_assessor','เดิม','master.view')")
    old = add_user(w.db, "old_assessor", "ไม่มีสิทธิ์ CAPA")
    w.db.execute("UPDATE notify_rules SET recipients='role:assessor,role:old_assessor' WHERE event='capa_done'"); w.db.commit()
    cid = w.capa()
    emit_capa(w.db, "capa_done", cid, w.owner)
    got = {r["user_id"] for r in w.notes()}
    assert w.asr_a in got and w.creator in got and w.asr_b not in got and old not in got and w.owner not in got


def test_capa_done_again_after_reopen_notifies_again(w):
    cid = w.capa()
    assert emit_capa(w.db, "capa_done", cid, w.owner) >= 1 and emit_capa(w.db, "capa_done", cid, w.owner) == 0
    w.db.execute("UPDATE capas SET reopen_count=1 WHERE id=?", (cid,)); w.db.commit()
    assert emit_capa(w.db, "capa_done", cid, w.owner) >= 1


def test_capa_due_soon_and_overdue_levels_catch_up_once_and_dedupe_across_runs(w):
    cid = w.capa(due_date="2026-03-20")
    run = lambda day: scheduler.run_daily(w.db, today=date(2026, 3, day), force=True, backup_dir=None, now=datetime(2026, 3, day, 8, 0))
    run(18)                                                                                        # เหลือ 2 วัน → due_soon (offset 2)
    assert [n["event"] for n in w.notes() if n["event"].startswith("capa_")] == ["capa_due_soon"]
    run(18)
    assert len([n for n in w.notes() if n["event"] == "capa_due_soon"]) == 1
    run(21)                                                                                        # เกิน 1 วัน → L1
    run(30)                                                                                        # ข้ามมาเกิน 10 วัน → L2 และ L3 ตามทัน ไม่ส่ง L1 ซ้ำ
    overdue = w.notes("capa_overdue")
    owner_hits = [n for n in overdue if n["user_id"] == w.owner]
    assert len(owner_hits) == 2                                                                    # L1 + L2 (owner อยู่ในกฎ L1,L2; L3 ไม่รวม owner)
    assert {n["user_id"] for n in overdue} >= {w.owner, w.head, w.admin}
    before = len(w.notes())
    run(30)
    assert len(w.notes()) == before


def test_closed_or_done_capa_gets_no_reminders(w):
    for status in ("done", "verified", "cancelled"):
        w.capa(due_date="2026-03-01", status=status)
    res = scheduler.run_daily(w.db, today=date(2026, 3, 30), force=True, now=datetime(2026, 3, 30, 8, 0))
    assert res["capa_notifications"] == 0 and w.notes() == []


def test_patrol_plan_due_and_overdue_notify_the_plan_inspector(w):
    pid = w.plan(inspector_id=w.insp)                                                              # ไม่เคยตรวจ → ครบตั้งแต่ 2026-03-01
    scheduler.run_daily(w.db, today=date(2026, 3, 1), force=True, now=datetime(2026, 3, 1, 8, 0))
    assert [(n["user_id"], n["event"], n["link"]) for n in w.notes() if n["event"].startswith("patrol_")] == [(w.insp, "patrol_due", f"/patrols?plan={pid}")]
    scheduler.run_daily(w.db, today=date(2026, 3, 3), force=True, now=datetime(2026, 3, 3, 8, 0))
    ov = [n for n in w.notes("patrol_overdue")]
    assert {n["user_id"] for n in ov} >= {w.insp, w.head} and len(ov) == len({(n["user_id"]) for n in ov})
    n_before = len(w.notes())
    scheduler.run_daily(w.db, today=date(2026, 3, 3), force=True, now=datetime(2026, 3, 3, 9, 0))
    assert len(w.notes()) == n_before


def test_patrol_without_inspector_falls_back_to_assessors_in_scope(w):
    w.plan()
    scheduler.run_daily(w.db, today=date(2026, 3, 1), force=True, now=datetime(2026, 3, 1, 8, 0))
    got = {n["user_id"] for n in w.notes("patrol_due")}
    assert got >= {w.creator, w.asr_a} and w.asr_b not in got and w.head not in got


def test_patrol_reminder_deduped_per_due_date_and_new_period_notifies_again(w):
    pid = w.plan(inspector_id=w.insp)
    assert emit_patrol(w.db, "patrol_due", pid, None, offset_days=0, days=0, due="2026-03-08") == 1
    assert emit_patrol(w.db, "patrol_due", pid, None, offset_days=0, days=0, due="2026-03-08") == 0
    assert emit_patrol(w.db, "patrol_due", pid, None, offset_days=0, days=0, due="2026-03-15") == 1


def test_messages_contain_no_free_text_notes(w):
    cid = w.capa(done_note="ให้นายสมชายตรวจแล้ว", verify_note="นางสมหญิงยืนยัน", description="รายละเอียดลับ นายสมศักดิ์")
    row = w.db.execute("SELECT * FROM capas WHERE id=?", (cid,)).fetchone()
    for event in ("capa_assigned", "capa_done", "capa_verified", "capa_reopened", "capa_due_soon", "capa_overdue"):
        head, body = render_capa_message(event, row, {"area": "พื้นที่ A", "days": 2})
        assert "สม" not in head + body and "ติดตั้งการ์ด" in head + body, event
    plan = w.db.execute("SELECT * FROM patrol_plans WHERE id=?", (w.plan(),)).fetchone()
    head, body = render_patrol_message("patrol_due", plan, {"area": "พื้นที่ A", "checklist": "ตรวจเครื่องจักร", "due": "2026-03-08", "days": 0})
    assert "ตรวจเครื่องจักร" in head + body and "2026-03-08" in head + body


def test_scheduler_failure_in_one_capa_does_not_stop_the_run(w, monkeypatch):
    first, second = w.capa(due_date="2026-03-01"), w.capa(due_date="2026-03-01")
    from safety import notify
    real = notify.emit_capa
    calls = []
    def flaky(db, event, capa_id, *a, **k):
        calls.append(capa_id)
        if capa_id == first:
            raise RuntimeError("boom")
        return real(db, event, capa_id, *a, **k)
    monkeypatch.setattr(notify, "emit_capa", flaky)
    res = scheduler.run_daily(w.db, today=date(2026, 3, 2), force=True, now=datetime(2026, 3, 2, 8, 0))
    assert res["errors"] >= 1 and second in calls and res["capa_notifications"] >= 1
    assert w.db.execute("SELECT finished_at FROM job_runs WHERE job='daily'").fetchone()[0]


def test_hazard_reminders_unchanged_by_the_new_loops(w):
    assert scheduler.run_daily(w.db, today=date(2026, 3, 2), force=True, now=datetime(2026, 3, 2, 8, 0))["notifications"] == 0


def test_save_attachment_patrol_scope_limit_5_per_answer(w, tmp_path):
    rid = w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id) VALUES('PT-1',?,?,?)", (w.cl, w.a["a"], w.insp)).lastrowid
    aid = w.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th) VALUES(?, 1, 's', 't')", (rid,)).lastrowid
    aid2 = w.db.execute("INSERT INTO checklist_items(checklist_id, text_th) VALUES(?, 'ข้อสอง')", (w.cl,)).lastrowid
    other = w.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th) VALUES(?, ?, 's', 't')", (rid, aid2)).lastrowid
    w.db.commit()
    for _ in range(5):
        images.save_attachment(w.db, str(tmp_path), aid, "before", "a.png", png(), w.insp, scope="patrol")
    with pytest.raises(images.ImageError):
        images.save_attachment(w.db, str(tmp_path), aid, "before", "a.png", png(), w.insp, scope="patrol")
    images.save_attachment(w.db, str(tmp_path), other, "before", "a.png", png(), w.insp, scope="patrol")           # จำกัดต่อข้อ ไม่ใช่ต่อรอบ
    assert w.db.execute("SELECT COUNT(*) FROM patrol_attachments WHERE answer_id=?", (aid,)).fetchone()[0] == 5
    assert (tmp_path / f"patrol-{aid}").is_dir()
