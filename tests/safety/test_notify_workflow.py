from datetime import timedelta

import pytest

from safety import notify, workflow
from safety.db import connect
from tests.safety.factories import add_user, user_row
from tests.safety.test_workflow import TODAY, W, fixed_today, w  # noqa: F401  (fixture w / fixed_today)


def notes(w, event=None):
    q = "SELECT n.*, u.name AS who FROM notifications n JOIN safety_users u ON u.id = n.user_id"
    a = ()
    if event:
        q, a = q + " WHERE n.event=?", (event,)
    return w.db.execute(q + " ORDER BY n.id", a).fetchall()


def who(w, event):
    return sorted(r["who"] for r in notes(w, event))


def test_each_transition_creates_expected_notifications(w):
    hid = w.hazard("rep")
    assert who(w, "hazard_reported") == ["ผู้ประเมิน", "ผู้ประเมิน 2"]                      # assessor ที่ขอบเขตครอบคลุม; ผู้ประเมินโรงงาน B ไม่ได้
    workflow.assess(w.db, w.u("asr"), hid, 5, 5)
    assert who(w, "hazard_assessed") == ["แอดมิน"]                                          # คะแนน 25 ≥ 17 → dept_head/admin
    workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], "")
    assert who(w, "hazard_assigned") == ["ผู้ประเมิน 2"]
    workflow.close(w.db, w.u("admin"), hid, "แก้แล้ว")
    assert who(w, "hazard_closed") == ["ผู้แจ้ง"]
    link = notes(w, "hazard_closed")[0]
    assert link["link"] == f"/hazards/{hid}" and "แก้แล้ว" in link["body"]
    h2 = w.hazard("rep")
    workflow.reject(w.db, w.u("asr"), h2, "รายการซ้ำ")
    rejected = notes(w, "hazard_rejected")
    assert [r["who"] for r in rejected] == ["ผู้แจ้ง"] and "รายการซ้ำ" in rejected[0]["body"]


def test_low_score_assessment_does_not_notify_leaders(w):
    hid = w.hazard()
    workflow.assess(w.db, w.u("asr"), hid, 1, 1)
    assert notes(w, "hazard_assessed") == []


def test_lost_race_emits_nothing(w, monkeypatch):
    hid = w.hazard()
    before = len(notes(w))
    real = workflow.visible_hazard

    def racing(db, user, hazard_id):
        row = real(db, user, hazard_id)
        db.execute("UPDATE hazards SET status='closed', version=version+1 WHERE id=?", (hazard_id,)); db.commit()
        return row

    monkeypatch.setattr(workflow, "visible_hazard", racing)
    assert workflow.assess(w.db, w.u("asr"), hid, 5, 5)
    assert len(notes(w)) == before and notes(w, "hazard_assessed") == []


def test_failed_validation_emits_nothing(w):
    hid = w.hazard()
    before = len(notes(w))
    workflow.assess(w.db, w.u("asr"), hid, 9, 9); workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], "")
    workflow.close(w.db, w.u("asr"), hid, "x"); workflow.reject(w.db, w.u("asr"), hid, "")
    assert workflow.report(w.db, w.u("rep"), w.form(title=""))[0] is None
    assert len(notes(w)) == before


def test_notifications_commit_together_with_the_transition(w, tmp_path):
    hid = w.hazard()
    workflow.assess(w.db, w.u("asr"), hid, 5, 5)
    other = connect(w.db.execute("PRAGMA database_list").fetchone()[2])                   # อีกการเชื่อมต่อเห็นทั้งสถานะและแจ้งเตือนพร้อมกัน
    assert other.execute("SELECT status FROM hazards WHERE id=?", (hid,)).fetchone()[0] == "assessed"
    assert other.execute("SELECT COUNT(*) FROM notifications WHERE event='hazard_assessed'").fetchone()[0] == 1


def test_reassign_notifies_new_owner_once_and_same_owner_again_is_deduped(w):
    hid = w.assessed(2, 2)
    workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], str(TODAY + timedelta(days=5)))
    workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], str(TODAY + timedelta(days=5)))          # เหมือนเดิมทุกอย่าง
    assert who(w, "hazard_assigned") == ["ผู้ประเมิน 2"]
    new_owner = add_user(w.db, "assessor", "เจ้าของใหม่")
    workflow.assign(w.db, w.u("asr"), hid, new_owner, str(TODAY + timedelta(days=5)))
    assert who(w, "hazard_assigned") == ["ผู้ประเมิน 2", "เจ้าของใหม่"]
    workflow.assign(w.db, w.u("asr"), hid, new_owner, str(TODAY + timedelta(days=9)))              # เลื่อนกำหนด
    assert len(notes(w, "hazard_assigned")) == 3


def test_emit_failure_does_not_block_transition(w, monkeypatch):
    monkeypatch.setattr(notify, "_queue_external", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    w.db.execute("UPDATE safety_users SET telegram_chat_id='1', notify_channels='inapp,telegram' WHERE role='assessor'"); w.db.commit()
    hid, errors = workflow.report(w.db, w.u("rep"), w.form())
    assert errors == [] and w.row(hid)["status"] == "reported" and notes(w) == []
    assert workflow.assess(w.db, w.u("asr"), hid, 5, 5) == [] and w.row(hid)["status"] == "assessed"


def test_actor_not_notified_for_own_action(w):
    hid = w.hazard("rep")
    workflow.assess(w.db, w.u("admin"), hid, 5, 5)                                         # แอดมินเป็นผู้ประเมินเอง
    assert notes(w, "hazard_assessed") == []
    h2 = w.hazard("rep")
    workflow.reject(w.db, w.u("rep"), h2, "x")                                              # ผู้แจ้งไม่มีสิทธิ์ปฏิเสธ — ไม่เกิดอะไร
    assert notes(w, "hazard_rejected") == []


def test_report_by_assessor_does_not_notify_self(w):
    hid, errors = workflow.report(w.db, w.u("asr"), w.form())
    assert errors == []
    assert "ผู้ประเมิน" not in who(w, "hazard_reported") and "ผู้ประเมิน 2" in who(w, "hazard_reported")


def test_external_outbox_rows_written_with_transition(w):
    w.db.execute("UPDATE safety_users SET telegram_chat_id='777', notify_channels='inapp,telegram' WHERE id=?", (w.ids["asr2"],)); w.db.commit()
    hid = w.hazard("rep")
    rows = [tuple(r) for r in w.db.execute("SELECT channel, target, status FROM notify_outbox")]
    assert rows == [("telegram", "777", "pending")]
