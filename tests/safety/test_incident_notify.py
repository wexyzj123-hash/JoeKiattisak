import os

import pytest
from PIL import Image

from safety import images, notify
from safety.db import connect
from safety.notify import emit, emit_incident, render_incident_message
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_user, severity_id
from tests.safety.helpers import login


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        self.rep = add_user(db, "reporter", "ผู้แจ้ง")
        self.asr_a = add_user(db, "assessor", "ผู้ประเมิน A", factory_id=self.a["f"])
        self.asr_b = add_user(db, "assessor", "ผู้ประเมิน B", factory_id=self.b["f"])
        self.inv = add_user(db, "assessor", "ผู้สอบสวน")

    def inc(self, chain="a", **kw):
        return add_incident(self.db, getattr(self, chain)["a"], self.rep, **kw)

    def notes(self):
        return self.db.execute("SELECT * FROM notifications ORDER BY id").fetchall()


@pytest.fixture
def w(db):
    return W(db)


def png(path_or_bytes=None):
    import io
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


# ---------- รูปภาพ ----------
def test_save_attachment_incident_scope_writes_row_and_folder(w, tmp_path):
    iid = w.inc()
    aid = images.save_attachment(w.db, str(tmp_path), iid, "before", "../x.png", png(), w.rep, scope="incident")
    row = w.db.execute("SELECT * FROM incident_attachments WHERE id=?", (aid,)).fetchone()
    assert row["incident_id"] == iid and row["orig_name"] == "x.png" and row["stored_name"].endswith(".jpg")
    assert os.path.isfile(tmp_path / f"incident-{iid}" / row["stored_name"])
    assert w.db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 0


def test_incident_attachment_limit_per_kind(w, tmp_path):
    iid = w.inc()
    for _ in range(images.MAX_PER_KIND):
        images.save_attachment(w.db, str(tmp_path), iid, "before", "a.png", png(), w.rep, scope="incident")
    with pytest.raises(images.ImageError):
        images.save_attachment(w.db, str(tmp_path), iid, "before", "a.png", png(), w.rep, scope="incident")


def test_unknown_scope_raises_and_hazard_scope_still_default(w, tmp_path):
    with pytest.raises(ValueError):
        images.save_attachment(w.db, str(tmp_path), 1, "before", "a.png", png(), w.rep, scope="x; DROP TABLE hazards")
    hid = add_hazard(w.db, w.a["a"], w.rep)
    images.save_attachment(w.db, str(tmp_path), hid, "before", "a.png", png(), w.rep)
    assert w.db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 1


# ---------- กฎการแจ้งเตือน ----------
def test_rules_table_rebuilt_keeping_ids_and_admin_edits(tmp_path):
    con = connect(str(tmp_path / "old.db"))
    ensure_schema(con)
    con.execute("DROP TABLE notify_rules")
    old_events = "'hazard_reported','hazard_assessed','hazard_assigned','hazard_closed','hazard_rejected','due_soon','overdue'"
    con.execute(f"CREATE TABLE notify_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL CHECK (event IN ({old_events})), "
                "recipients TEXT NOT NULL DEFAULT '', channels TEXT NOT NULL DEFAULT 'inapp', offset_days INTEGER NOT NULL DEFAULT 0, "
                "min_score INTEGER, enabled INTEGER NOT NULL DEFAULT 1)")
    con.execute("INSERT INTO notify_rules(id,event,recipients,channels,offset_days,min_score,enabled) VALUES(5,'overdue','owner','inapp,line',3,NULL,0)")
    con.execute("INSERT INTO notify_rules(id,event,recipients,channels,offset_days,min_score,enabled) VALUES(9,'hazard_assessed','role:admin','inapp',0,12,1)")
    con.commit()
    before = [tuple(r) for r in con.execute("SELECT * FROM notify_rules ORDER BY id")]
    ensure_schema(con); ensure_schema(con)
    kept = [tuple(r) for r in con.execute("SELECT * FROM notify_rules WHERE id IN (5,9) ORDER BY id")]
    assert kept == before
    con.execute("INSERT INTO notify_rules(event,recipients) VALUES('incident_reported','role:admin')")
    con.commit()
    assert "incident_reported" in con.execute("SELECT sql FROM sqlite_master WHERE name='notify_rules'").fetchone()[0]


def test_rebuild_keeps_autoincrement_sequence_and_future_event_lists_trigger_a_rebuild(tmp_path, monkeypatch):
    import safety.schema as schema
    con = connect(str(tmp_path / "seq.db"))
    ensure_schema(con)
    ids = [r[0] for r in con.execute("SELECT id FROM notify_rules ORDER BY id")]
    top = max(ids)
    con.execute("DELETE FROM notify_rules WHERE id=?", (top,)); con.commit()                    # แอดมินลบกฎล่าสุด (ลำดับ AUTOINCREMENT ยังจำไว้)
    monkeypatch.setattr(schema, "NOTIFY_EVENTS", schema.NOTIFY_EVENTS + ("future_event",))     # เฟสถัดไปเพิ่มเหตุการณ์ → ต้องสร้างตารางใหม่
    schema.rebuild_notify_rules_if_needed(con)
    assert "future_event" in con.execute("SELECT sql FROM sqlite_master WHERE name='notify_rules'").fetchone()[0]
    new_id = con.execute("INSERT INTO notify_rules(event,recipients) VALUES('future_event','owner')").lastrowid
    assert new_id == top + 1                                                                      # ไม่ใช้รหัสของกฎที่ถูกลบซ้ำ (กัน dedupe key ชน)


def test_incident_message_does_not_copy_free_text_closing_note(w):
    iid = w.inc(closing_note="นายสมชาย กลับมาทำงานแล้ว")
    head, body = render_incident_message("incident_closed", w.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone(), {"area": "พื้นที่ A"})
    assert "สมชาย" not in head + body and "เหตุทดสอบ" in body


def test_incident_notifications_only_reach_people_who_can_open_the_incident(w):
    w.db.execute("INSERT INTO safety_roles(key,name_th,permissions) VALUES('old_assessor','ผู้ประเมินเดิม','master.view,hazard.report,hazard.assess')")
    old = add_user(w.db, "old_assessor", "ไม่มีสิทธิ์อุบัติเหตุ")
    w.db.commit()
    w.db.execute("UPDATE notify_rules SET recipients='role:old_assessor,role:assessor' WHERE event='incident_reported'"); w.db.commit()
    emit_incident(w.db, "incident_reported", w.inc(), w.rep)
    got = {r["user_id"] for r in w.notes()}
    assert old not in got and w.asr_a in got


def test_reassigning_back_or_changing_due_date_notifies_again(w):
    iid = w.inc(owner_id=w.inv, due_date="2026-03-20")
    assert emit_incident(w.db, "incident_assigned", iid, w.asr_a) == 1
    w.db.execute("UPDATE incidents SET owner_id=? WHERE id=?", (w.asr_a, iid)); w.db.commit()
    assert emit_incident(w.db, "incident_assigned", iid, w.inv) == 1
    w.db.execute("UPDATE incidents SET owner_id=? WHERE id=?", (w.inv, iid)); w.db.commit()
    assert emit_incident(w.db, "incident_assigned", iid, w.asr_a) == 0               # กลับมาเป็นคนเดิม วันเดิม → ซ้ำกับครั้งแรก ไม่แจ้งซ้ำ
    w.db.execute("UPDATE incidents SET due_date='2026-03-25' WHERE id=?", (iid,)); w.db.commit()
    assert emit_incident(w.db, "incident_assigned", iid, w.asr_a) == 1               # เลื่อนวัน → แจ้งใหม่


def test_default_incident_rules_seeded_once(db):
    rows = db.execute("SELECT event, recipients, channels FROM notify_rules WHERE event LIKE 'incident_%' ORDER BY event").fetchall()
    assert [(r["event"], r["recipients"]) for r in rows] == [("incident_assigned", "owner"), ("incident_closed", "reporter"),
                                                              ("incident_reported", "role:assessor")]
    db.execute("DELETE FROM notify_rules WHERE event LIKE 'incident_%'"); db.commit()
    ensure_schema(db)
    assert db.execute("SELECT COUNT(*) FROM notify_rules WHERE event LIKE 'incident_%'").fetchone()[0] == 0


def test_emit_incident_reported_notifies_assessors_in_scope_not_actor(w):
    iid = w.inc("a")
    assert emit_incident(w.db, "incident_reported", iid, w.inv) >= 1
    got = {r["user_id"] for r in w.notes()}
    assert w.asr_a in got and w.asr_b not in got and w.inv not in got and w.rep not in got
    row = w.notes()[0]
    assert row["incident_id"] == iid and row["hazard_id"] is None and row["link"] == f"/incidents/{iid}"


def test_emit_incident_dedupes_and_assigned_stage_changes_with_owner(w):
    iid = w.inc(owner_id=w.inv)
    assert emit_incident(w.db, "incident_assigned", iid, w.asr_a) == 1
    assert emit_incident(w.db, "incident_assigned", iid, w.asr_a) == 0
    w.db.execute("UPDATE incidents SET owner_id=? WHERE id=?", (w.asr_a, iid)); w.db.commit()
    assert emit_incident(w.db, "incident_assigned", iid, w.inv) == 1


def test_unknown_event_or_missing_incident_is_a_noop(w):
    assert emit_incident(w.db, "hazard_reported", 1, None) == 0 and emit_incident(w.db, "incident_reported", 9999, None) == 0


def test_messages_never_contain_people_names(w):
    iid = w.inc(title="รถตักชนเสา", severity=None)
    w.db.execute("INSERT INTO incident_people(incident_id, role, name, employee_no, note) VALUES(?,?,?,?,?)",
                 (iid, "injured", "สมชาย ใจดี", "E1234", "แขนหัก"))
    w.db.execute("UPDATE notify_rules SET channels='inapp,telegram' WHERE event='incident_reported'")
    w.db.execute("UPDATE safety_users SET telegram_chat_id='12345', notify_channels='inapp,telegram' WHERE id=?", (w.asr_a,))
    w.db.commit()
    emit_incident(w.db, "incident_reported", iid, w.rep)
    for table in ("notifications", "notify_outbox"):
        for r in w.db.execute(f"SELECT * FROM {table}").fetchall():
            blob = " ".join(str(v) for v in tuple(r))
            assert "สมชาย" not in blob and "E1234" not in blob and "แขนหัก" not in blob
    assert w.db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] >= 1
    head, body = render_incident_message("incident_reported", w.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone(), {"area": "พื้นที่ A"})
    assert "รถตักชนเสา" in body and "สมชาย" not in head + body


def test_lost_time_accident_is_urgent_in_outbox_and_minor_one_is_not(w):
    w.db.execute("UPDATE notify_rules SET channels='inapp,telegram' WHERE event='incident_reported'")
    w.db.execute("UPDATE safety_users SET telegram_chat_id='12345', notify_channels='inapp,telegram' WHERE id=?", (w.asr_a,))
    w.db.commit()
    lt = w.inc(injury_severity_id=severity_id(w.db, "lost_time"), code="IN-LT")
    fa = w.inc(injury_severity_id=severity_id(w.db, "first_aid"), code="IN-FA")
    emit_incident(w.db, "incident_reported", lt, w.rep); emit_incident(w.db, "incident_reported", fa, w.rep)
    urgent = {r["dedupe_key"].split(":")[1]: r["urgent"] for r in w.db.execute("SELECT * FROM notify_outbox")}
    assert urgent == {f"i{lt}": 1, f"i{fa}": 0}


def test_failure_in_incident_emit_does_not_break_caller(w, monkeypatch):
    iid = w.inc()
    monkeypatch.setattr(notify, "render_incident_message", lambda *a, **k: 1 / 0)
    w.db.execute("INSERT INTO incident_events(incident_id, kind) VALUES(?,?)", (iid, "reported"))
    assert emit_incident(w.db, "incident_reported", iid, w.rep) == 0
    w.db.commit()
    assert w.db.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == 1


def test_hazard_emit_unchanged_by_the_refactor(w):
    hid = add_hazard(w.db, w.a["a"], w.rep, title="น้ำมันหก")
    assert emit(w.db, "hazard_reported", hid, w.rep) >= 1
    assert w.notes()[0]["hazard_id"] == hid and w.notes()[0]["link"] == f"/hazards/{hid}" and w.notes()[0]["incident_id"] is None


def test_notify_admin_lists_new_events_in_thai(safety_app):
    html = login(safety_app, "admin").get("/admin/notifications").get_data(as_text=True)
    for text in ("แจ้งอุบัติเหตุใหม่", "มอบหมายผู้สอบสวน", "ปิดรายการอุบัติเหตุ"):
        assert text in html
