import json
import threading
from datetime import date, datetime, timedelta

import pytest

from safety import capa, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_user, severity_id, user_row

NOW = datetime(2026, 3, 10, 14, 30)
TODAY = NOW.date()


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
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
        u = lambda role, name, **kw: user_row(db, add_user(db, role, name, **kw))
        self.owner = u("reporter", "เจ้าของ")                      # ผู้แจ้งทั่วไป ไม่มีสิทธิ์ CAPA — ต้องทำงานของตนเองได้
        self.owner2 = u("reporter", "เจ้าของคนใหม่")
        self.creator = u("assessor", "ผู้สร้าง")
        self.verifier = u("assessor", "ผู้ยืนยัน")
        self.head = u("dept_head", "หัวหน้า")                       # capa.create เท่านั้น
        self.admin, self.admin2 = u("admin", "แอดมิน"), u("admin", "แอดมิน 2")
        self.other_b = u("assessor", "โรงงาน B", factory_id=self.b["f"])
        self.viewer = u("viewer", "ผู้ดู")

    def form(self, **kw):
        return {"title": "ติดตั้งการ์ดเครื่องจักร", "description": "รายละเอียด", "action_type": "corrective", "owner_id": str(self.owner["id"]),
                "due_date": (TODAY + timedelta(days=14)).isoformat(), "risk_area_id": str(self.a["a"]), **kw}

    def new(self, user=None, source=None, **kw):
        cid, errors = capa.create(self.db, user or self.creator, self.form(**kw), source)
        assert errors == [], errors
        return cid

    def row(self, cid):
        return self.db.execute("SELECT * FROM capas WHERE id=?", (cid,)).fetchone()

    def events(self, cid):
        return [e["kind"] for e in self.db.execute("SELECT kind FROM capa_events WHERE capa_id=? ORDER BY id", (cid,))]

    def notes(self, event):
        return self.db.execute("SELECT * FROM notifications WHERE event=?", (event,)).fetchall()

    def to_done(self, cid, by=None):
        assert capa.complete(self.db, by or self.owner, cid, "ทำเสร็จแล้ว") == []


@pytest.fixture
def w(db):
    return W(db)


def test_happy_path_statuses_versions_events_and_notifications(w):
    cid = w.new()
    r = w.row(cid)
    assert r["code"] == "CP-2603-0001" and (r["status"], r["version"], r["source_kind"], r["created_by"]) == ("open", 0, "manual", w.creator["id"])
    assert len(w.notes("capa_assigned")) == 1 and w.notes("capa_assigned")[0]["user_id"] == w.owner["id"]
    assert capa.start(w.db, w.owner, cid) == [] and w.row(cid)["status"] == "in_progress"
    assert capa.complete(w.db, w.owner, cid, "ติดตั้งเสร็จ") == []
    r = w.row(cid)
    assert (r["status"], r["done_by"], r["done_note"]) == ("done", w.owner["id"], "ติดตั้งเสร็จ") and r["done_at"]
    assert {n["user_id"] for n in w.notes("capa_done")} >= {w.creator["id"], w.verifier["id"]}
    assert capa.verify(w.db, w.verifier, cid, "ตรวจแล้ว", "2026-04-10") == []
    r = w.row(cid)
    assert (r["status"], r["verified_by"], r["verify_note"], r["effectiveness_due"], r["version"]) == ("verified", w.verifier["id"], "ตรวจแล้ว", "2026-04-10", 3)
    assert w.events(cid) == ["created", "started", "completed", "verified"]
    assert [n["user_id"] for n in w.notes("capa_verified")] == [w.owner["id"]]


@pytest.mark.parametrize("override, bad", [
    ({"title": ""}, True), ({"title": "ก" * 201}, True), ({"description": "ก" * 2001}, True), ({"action_type": "x"}, True),
    ({"due_date": "2026-03-09"}, True), ({"due_date": "2026-03-10"}, False), ({"due_date": "2028-03-09"}, False), ({"due_date": "2028-03-10"}, True),
    ({"due_date": "10/03/2026"}, True), ({"due_date": ""}, True), ({"owner_id": ""}, True), ({"owner_id": "9999"}, True), ({"risk_area_id": "9999"}, True),
])
def test_create_validation(w, override, bad):
    cid, errors = capa.create(w.db, w.creator, w.form(**override))
    assert (cid is None) is bad and bool(errors) is bad


def test_create_permission_owner_scope_and_active(w):
    assert capa.create(w.db, w.viewer, w.form()) == (None, ["คุณไม่มีสิทธิ์สร้าง CAPA"])
    assert capa.create(w.db, w.owner, w.form())[0] is None
    assert capa.create(w.db, w.head, w.form())[0]                                                      # head มี capa.create
    scoped_owner = user_row(w.db, add_user(w.db, "reporter", "ในโรงงาน B", factory_id=w.b["f"]))
    assert capa.create(w.db, w.creator, w.form(owner_id=str(scoped_owner["id"])))[0] is None            # พื้นที่ของโรงงาน A
    w.db.execute("UPDATE safety_users SET active=0 WHERE id=?", (w.owner2["id"],)); w.db.commit()
    assert capa.create(w.db, w.creator, w.form(owner_id=str(w.owner2["id"])))[0] is None
    assert capa.create(w.db, w.other_b, w.form())[0] is None                                           # ผู้สร้างนอกขอบเขตพื้นที่


def test_sources_hazard_incident_patrol_take_area_from_source_and_must_be_visible(w):
    hz = add_hazard(w.db, w.b["a"], w.owner["id"], title="สภาพอันตราย")
    cid = w.new(user=w.admin, source=("hazard", hz), risk_area_id="")
    assert (w.row(cid)["risk_area_id"], w.row(cid)["source_kind"], w.row(cid)["source_id"]) == (w.b["a"], "hazard", hz)
    assert capa.create(w.db, w.other_b, w.form(owner_id=str(w.other_b["id"])), ("hazard", hz))[0]          # ผู้สร้างในโรงงาน B เห็นอันตรายของ B
    assert capa.create(w.db, w.other_b, w.form(), ("hazard", add_hazard(w.db, w.a["a"], w.owner["id"], code="HZ-A")))[0] is None   # แต่ไม่เห็นของ A
    assert capa.create(w.db, w.creator, w.form(), ("hazard", 9999))[0] is None
    inc = add_incident(w.db, w.a["a"], w.owner["id"], injury_severity_id=severity_id(w.db, "first_aid"))
    assert w.row(w.new(source=("incident", inc)))["risk_area_id"] == w.a["a"]
    assert capa.create(w.db, w.creator, w.form(), ("incident", 9999))[0] is None
    assert capa.create(w.db, w.creator, w.form(), ("bogus", 1))[0] is None
    assert capa.create(w.db, w.creator, w.form(risk_area_id=""))[0] is None                            # manual ต้องระบุพื้นที่


def test_start_only_by_owner_and_only_from_open(w):
    cid = w.new()
    for user in (w.creator, w.verifier, w.admin, w.owner2):
        assert capa.start(w.db, user, cid), user["name"]
    assert capa.start(w.db, w.owner, cid) == [] and capa.start(w.db, w.owner, cid)


def test_complete_by_owner_or_capa_create_holder_not_others_and_note_required(w):
    cid = w.new()
    assert capa.complete(w.db, w.owner, cid, "  ") and capa.complete(w.db, w.owner, cid, "ก" * 2001)
    assert capa.complete(w.db, w.verifier, cid, "x") == []                                              # capa.create holder (assessor) ทำแทนได้
    cid2 = w.new()
    assert capa.complete(w.db, w.owner2, cid2, "x") == capa.NOT_FOUND                                    # คนนอกที่เปิดไม่ได้
    assert capa.complete(w.db, w.viewer, cid2, "x") == capa.NOT_FOUND


def test_verify_refused_for_current_or_past_owner_and_for_the_person_who_marked_it_done_even_if_admin(w):
    cid = w.new(owner_id=str(w.verifier["id"]))                                                          # ผู้ยืนยันเป็นเจ้าของ
    w.to_done(cid, by=w.verifier)
    assert capa.verify(w.db, w.verifier, cid, "ยืนยันเอง")[0].startswith("ผู้ที่เคยเป็นเจ้าของ")
    cid2 = w.new()
    assert capa.complete(w.db, w.admin, cid2, "แอดมินกดเสร็จแทน") == []
    assert "ไม่ใช่ผู้ที่กดว่าเสร็จ" in capa.verify(w.db, w.admin, cid2, "ยืนยันเอง")[0]
    assert capa.verify(w.db, w.admin2, cid2, "แอดมินอีกคน") == [] and w.row(cid2)["verified_by"] == w.admin2["id"]
    cid3 = w.new()
    w.to_done(cid3)
    assert capa.verify(w.db, w.owner, cid3, "x") == ["คุณไม่มีสิทธิ์ยืนยันผล CAPA"]                         # เจ้าของทั่วไปไม่มี capa.verify
    assert capa.verify(w.db, w.head, cid3, "x") == ["คุณไม่มีสิทธิ์ยืนยันผล CAPA"]                          # head ไม่มี capa.verify


def test_verify_via_reassignment_history_and_validation(w):
    cid = w.new()
    assert capa.reassign(w.db, w.creator, cid, w.verifier["id"], "") == []                               # ผู้ยืนยันเคยเป็นเจ้าของ
    assert capa.reassign(w.db, w.creator, cid, w.owner2["id"], "") == []                                  # แล้วโอนออก
    w.to_done(cid, by=w.owner2)
    assert capa.verify(w.db, w.verifier, cid, "x") and w.row(cid)["status"] == "done"                    # ประวัติยังนับ
    assert capa.verify(w.db, w.admin, cid, "ก" * 1001) and capa.verify(w.db, w.admin, cid, "x", "2026-03-09") and capa.verify(w.db, w.admin, cid, "x", "bad")
    assert capa.verify(w.db, w.admin, cid, "x", "2028-03-11")                                            # เกิน 730 วัน
    assert capa.verify(w.db, w.admin, cid, "x", "") == [] and w.row(cid)["effectiveness_due"] is None


def test_effectiveness_recorded_once_by_verifier_rights_after_due_date(w, monkeypatch):
    cid = w.new(); w.to_done(cid)
    capa.verify(w.db, w.verifier, cid, "ok", "2026-03-20")
    assert capa.record_effectiveness(w.db, w.verifier, cid, "effective", "x") == ["ยังไม่ถึงกำหนดตรวจประสิทธิผล (2026-03-20)"]
    monkeypatch.setattr(timeutil, "today", lambda: date(2026, 3, 20))
    assert capa.record_effectiveness(w.db, w.admin, cid, "maybe", "x") and capa.record_effectiveness(w.db, w.admin, cid, "effective", "ก" * 501)
    assert capa.record_effectiveness(w.db, w.owner, cid, "effective", "x") == ["คุณไม่มีสิทธิ์บันทึกผลการตรวจประสิทธิผล"]
    assert capa.record_effectiveness(w.db, w.verifier, cid, "ineffective", "ยังเกิดซ้ำ") == []
    assert capa.record_effectiveness(w.db, w.admin, cid, "effective", "แก้ทับ") and w.row(cid)["effectiveness_result"] == "ineffective"
    assert w.events(cid)[-1] == "effectiveness"
    nodue = w.new(); w.to_done(nodue); capa.verify(w.db, w.verifier, nodue, "", "")
    assert capa.record_effectiveness(w.db, w.verifier, nodue, "effective", "x")                        # ไม่ได้ตั้งกำหนด → ไม่มีให้บันทึก


def test_reopen_loop_counts_requires_reason_and_notifies_owner(w):
    cid = w.new(); w.to_done(cid)
    assert capa.reopen(w.db, w.verifier, cid, " ") and capa.reopen(w.db, w.verifier, cid, "ก" * 501)
    assert capa.reopen(w.db, w.owner, cid, "x") == ["คุณไม่มีสิทธิ์ยืนยันผล CAPA"]
    assert capa.reopen(w.db, w.verifier, cid, "ยังไม่ครบ") == []
    r = w.row(cid)
    assert (r["status"], r["reopen_count"], r["done_by"]) == ("in_progress", 1, w.owner["id"]) and [n["user_id"] for n in w.notes("capa_reopened")] == [w.owner["id"]]
    w.to_done(cid)
    assert len(w.notes("capa_done")) >= 2                                                                # เสร็จรอบสองแจ้งเตือนอีกครั้ง
    capa.reopen(w.db, w.verifier, cid, "ยังไม่ครบอีก")
    assert w.row(cid)["reopen_count"] == 2 and w.events(cid).count("reopened") == 2


def test_cancel_rules(w):
    cid = w.new()
    assert capa.cancel(w.db, w.owner, cid, "x") == ["คุณไม่มีสิทธิ์ยกเลิก CAPA"] and capa.cancel(w.db, w.creator, cid, " ") and capa.cancel(w.db, w.creator, cid, "ก" * 501)
    assert capa.cancel(w.db, w.creator, cid, "ซ้ำซ้อน") == [] and (w.row(cid)["status"], w.row(cid)["cancel_reason"]) == ("cancelled", "ซ้ำซ้อน")
    done = w.new(); w.to_done(done)
    assert capa.cancel(w.db, w.creator, done, "x")                                                       # done แล้วยกเลิกไม่ได้ (ต้องตีกลับ)
    ver = w.new(); w.to_done(ver); capa.verify(w.db, w.verifier, ver, "")
    assert capa.cancel(w.db, w.creator, ver, "x")


def test_reassign_updates_owner_notifies_new_owner_and_keeps_history(w):
    cid = w.new()
    assert capa.reassign(w.db, w.owner, cid, w.owner2["id"], "") == ["คุณไม่มีสิทธิ์มอบหมาย CAPA"]
    for bad in (9999, "abc", "", w.other_b["id"]):
        assert capa.reassign(w.db, w.creator, cid, bad, ""), bad
    assert capa.reassign(w.db, w.creator, cid, w.owner2["id"], "2026-04-01") == []
    r = w.row(cid)
    assert (r["owner_id"], r["due_date"], r["status"], r["version"]) == (w.owner2["id"], "2026-04-01", "open", 1)
    assert [n["user_id"] for n in w.notes("capa_assigned")] == [w.owner["id"], w.owner2["id"]]
    assert capa.owners_ever(w.db, r) == {w.owner["id"], w.owner2["id"]}
    assert capa.reassign(w.db, w.creator, cid, w.owner2["id"], "2026-03-01")                             # วันที่อดีตไม่ได้
    w.to_done(cid, by=w.owner2)
    assert capa.reassign(w.db, w.creator, cid, w.owner["id"], "")                                         # done แล้วโอนไม่ได้


def test_two_connections_race_complete_versus_cancel_exactly_one_wins(tmp_path):
    path = str(tmp_path / "race.db")
    con = connect(path); ensure_schema(con)
    world = W(con)
    cid = world.new()
    results, barrier = {}, threading.Barrier(2)

    def run(name, fn):
        c = connect(path)
        try:
            barrier.wait()
            results[name] = fn(c)
        finally:
            c.close()
    ts = [threading.Thread(target=run, args=("complete", lambda c: capa.complete(c, user_row(c, world.creator["id"]), cid, "เสร็จ"))),
          threading.Thread(target=run, args=("cancel", lambda c: capa.cancel(c, user_row(c, world.creator["id"]), cid, "ยกเลิก")))]
    [t.start() for t in ts]; [t.join() for t in ts]
    wins = [k for k, v in results.items() if v == []]
    assert len(wins) == 1, results
    assert con.execute("SELECT status FROM capas WHERE id=?", (cid,)).fetchone()[0] == {"complete": "done", "cancel": "cancelled"}[wins[0]]
    assert con.execute("SELECT COUNT(*) FROM capa_events WHERE kind IN ('completed','cancelled')").fetchone()[0] == 1


def test_double_click_complete_makes_one_event_and_one_notification(w):
    cid = w.new()
    assert capa.complete(w.db, w.owner, cid, "ok") == [] and capa.complete(w.db, w.owner, cid, "ok")
    assert w.events(cid).count("completed") == 1 and len({(n["user_id"]) for n in w.notes("capa_done")}) == len(w.notes("capa_done"))


def test_stale_version_loses_race_and_writes_nothing(w):
    cid = w.new()
    stale = w.row(cid)
    capa.start(w.db, w.owner, cid)
    assert capa._transition(w.db, stale, {"owner_id": w.owner2["id"]}, "reassigned", w.creator, "open") == capa.LOST_RACE
    assert w.row(cid)["owner_id"] == w.owner["id"] and w.events(cid) == ["created", "started"]


def test_terminal_states_refuse_everything(w):
    cancelled = w.new(); capa.cancel(w.db, w.creator, cancelled, "ซ้ำ")
    verified = w.new(); w.to_done(verified); capa.verify(w.db, w.verifier, verified, "")
    for cid in (cancelled, verified):
        for call in (lambda c=cid: capa.start(w.db, w.owner, c), lambda c=cid: capa.complete(w.db, w.owner, c, "x"), lambda c=cid: capa.verify(w.db, w.admin2, c, "x"),
                     lambda c=cid: capa.reopen(w.db, w.admin2, c, "x"), lambda c=cid: capa.cancel(w.db, w.creator, c, "x"),
                     lambda c=cid: capa.reassign(w.db, w.creator, c, w.owner2["id"], "")):
            assert call(), (cid, call)


def test_visibility_owner_creator_scope_and_view_all_else_not_found(w):
    cid = w.new()
    seen = lambda u: capa.visible_capa(w.db, u, cid) is not None
    assert seen(w.owner) and seen(w.creator) and seen(w.verifier) and seen(w.head) and seen(w.admin)     # เจ้าของ/ผู้สร้าง/ผู้มี capa.create|verify
    assert not seen(w.owner2) and not seen(w.viewer) and not seen(w.other_b)
    assert capa.visible_capa(w.db, w.admin, 9999) is None
    for call in (lambda: capa.start(w.db, w.other_b, cid), lambda: capa.complete(w.db, w.other_b, cid, "x"), lambda: capa.verify(w.db, w.other_b, cid, "x"),
                 lambda: capa.cancel(w.db, w.other_b, cid, "x"), lambda: capa.reassign(w.db, w.other_b, cid, w.owner["id"], "")):
        assert call() == capa.NOT_FOUND


def test_gate_helpers_default_off_and_on(w):
    hz = add_hazard(w.db, w.a["a"], w.owner["id"])
    open_ = w.new(source=("hazard", hz)); done = w.new(source=("hazard", hz)); w.to_done(done)
    ver = w.new(source=("hazard", hz)); w.to_done(ver); capa.verify(w.db, w.verifier, ver, "")
    can = w.new(source=("hazard", hz)); capa.cancel(w.db, w.creator, can, "ไม่จำเป็น")
    w.new(source=("incident", add_incident(w.db, w.a["a"], w.owner["id"])))
    assert capa.open_blockers(w.db, "hazard", hz) == 2 and capa.open_blockers(w.db, "incident", 999) == 0 and capa.open_blockers(w.db, "patrol", hz) == 0
    assert capa.gate_enabled(w.db) is False and capa.gate_errors(w.db, "hazard", hz) == []
    w.db.execute("UPDATE app_settings SET value='1' WHERE key='require_capa_verified'"); w.db.commit()
    assert capa.gate_enabled(w.db) is True
    errs = capa.gate_errors(w.db, "hazard", hz)
    assert len(errs) == 1 and "2" in errs[0] and capa.gate_errors(w.db, "hazard", 999) == []
