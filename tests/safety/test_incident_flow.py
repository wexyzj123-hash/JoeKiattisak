import json
import threading
from datetime import date, datetime, timedelta

import pytest

from safety import incident_flow as flow
from safety import incidents, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_user, severity_id, user_row

NOW = datetime(2026, 3, 10, 14, 30)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
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
        self.rep = u("reporter", "ผู้แจ้ง")
        self.inv = u("assessor", "ผู้สอบสวน 1")
        self.inv2 = u("assessor", "ผู้สอบสวน 2")
        self.inv_b = u("assessor", "ผู้สอบสวน B", factory_id=self.b["f"])
        self.head = u("dept_head", "หัวหน้า")
        self.viewer = u("viewer", "ผู้ดู")
        self.admin = u("admin", "แอดมิน")
        self.admin2 = u("admin", "แอดมิน 2")
        self.cause = db.execute("SELECT id FROM incident_causes LIMIT 1").fetchone()[0]

    def new(self, severity="first_aid", kind="accident", **extra):
        form = {"kind": kind, "occurred_at": "2026-03-09T08:00", "risk_area_id": str(self.a["a"]), "title": "เหตุทดสอบ",
                "injury_severity_id": str(severity_id(self.db, severity)), "likelihood": "3", "severity": "3", **extra}
        iid, errors = incidents.report(self.db, self.rep, form)
        assert errors == []
        return iid

    def row(self, iid):
        return self.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()

    def events(self, iid):
        return [e["kind"] for e in self.db.execute("SELECT kind FROM incident_events WHERE incident_id=? ORDER BY id", (iid,))]

    def notes(self, event):
        return self.db.execute("SELECT * FROM notifications WHERE event=?", (event,)).fetchall()

    def to_actions_set(self, iid, investigator=None, lost="3"):
        inv = investigator or self.inv
        assert flow.assign_investigator(self.db, self.admin, iid, inv["id"], "") == []
        assert flow.record_investigation(self.db, inv, iid, self.cause, "สาเหตุราก", "มาตรการ", lost) == []


@pytest.fixture
def w(db):
    return W(db)


def test_full_happy_path_statuses_versions_events_and_notifications(w):
    iid = w.new()
    assert w.row(iid)["status"] == "reported"
    assert flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "2026-03-20", "ฝากด้วย") == []
    r = w.row(iid)
    assert (r["status"], r["owner_id"], r["due_date"], r["version"]) == ("investigating", w.inv["id"], "2026-03-20", 1)
    assert len(w.notes("incident_assigned")) == 1
    assert flow.record_investigation(w.db, w.inv, iid, w.cause, "ขาดการตรวจ", "ติดตั้งการ์ด", "") == []
    r = w.row(iid)
    assert (r["status"], r["root_cause"], r["corrective_action"], r["cause_id"], r["version"]) == ("actions_set", "ขาดการตรวจ", "ติดตั้งการ์ด", w.cause, 2)
    assert flow.close(w.db, w.admin, iid, "ติดตั้งแล้ว") == []
    r = w.row(iid)
    assert (r["status"], r["closing_note"], r["version"]) == ("closed", "ติดตั้งแล้ว", 3)
    assert w.events(iid) == ["reported", "assigned", "investigated", "closed"]
    assert len(w.notes("incident_closed")) == 1 and w.notes("incident_closed")[0]["user_id"] == w.rep["id"]


def test_assign_defaults_due_date_when_blank_and_rejects_bad_dates(w):
    iid = w.new()
    for bad in ("2026-03-09", "2027-03-11", "2026-02-30", "10/03/2026", "x"):
        assert flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], bad), bad
    assert flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "") == []
    assert w.row(iid)["due_date"] == (NOW.date() + timedelta(days=14)).isoformat()


def test_assign_rejects_out_of_scope_inactive_unprivileged_and_unknown_targets(w):
    iid = w.new()
    w.db.execute("UPDATE safety_users SET active=0 WHERE id=?", (w.inv2["id"],)); w.db.commit()
    for target in (w.inv_b["id"], w.inv2["id"], w.rep["id"], w.viewer["id"], 9999, "abc", "", None):
        assert flow.assign_investigator(w.db, w.admin, iid, target, ""), target
    assert w.row(iid)["status"] == "reported" and w.events(iid) == ["reported"]


def test_assign_requires_permission_and_visibility(w):
    iid = w.new()
    assert flow.assign_investigator(w.db, w.rep, iid, w.inv["id"], "") == ["คุณไม่มีสิทธิ์มอบหมายผู้สอบสวน"]
    assert flow.assign_investigator(w.db, w.inv_b, iid, w.inv["id"], "") == flow.NOT_FOUND            # นอกขอบเขต → ไม่รู้ว่ามีรายการ
    assert flow.assign_investigator(w.db, w.admin, 9999, w.inv["id"], "") == flow.NOT_FOUND


def test_reassign_updates_owner_and_stage_notifies_new_owner(w):
    iid = w.new()
    assert flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "") == []
    assert flow.assign_investigator(w.db, w.admin, iid, w.inv2["id"], "") == []
    assert [n["user_id"] for n in w.notes("incident_assigned")] == [w.inv["id"], w.inv2["id"]]


def test_record_only_by_current_investigator_or_admin(w):
    iid = w.new()
    flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "")
    args = (iid, w.cause, "ราก", "มาตรการ", "")
    assert flow.record_investigation(w.db, w.inv2, *args)
    assert flow.record_investigation(w.db, w.viewer, *args)
    assert flow.record_investigation(w.db, w.inv, *args) == []
    assert flow.record_investigation(w.db, w.admin, *args) == []                                       # แก้ซ้ำใน actions_set โดยแอดมิน


def test_record_validation(w):
    iid = w.new()
    flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "")
    bad = [(9999, "ราก", "ม", ""), ("x", "ราก", "ม", ""), (w.cause, "", "ม", ""), (w.cause, "ราก", " ", ""), (w.cause, "ก" * 2001, "ม", ""),
           (w.cause, "ราก", "ก" * 2001, ""), (w.cause, "ราก", "ม", "abc"), (w.cause, "ราก", "ม", "-1"), (w.cause, "ราก", "ม", "99999")]
    for cause, root, act, lost in bad:
        assert flow.record_investigation(w.db, w.inv, iid, cause, root, act, lost), (cause, root, act, lost)
    w.db.execute("UPDATE incident_causes SET active=0 WHERE id=?", (w.cause,)); w.db.commit()
    assert flow.record_investigation(w.db, w.inv, iid, w.cause, "ราก", "ม", "")
    assert w.row(iid)["status"] == "investigating" and w.events(iid) == ["reported", "assigned"]


def test_lost_time_accident_requires_lost_days(w):
    iid = w.new("lost_time")
    flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "")
    assert flow.record_investigation(w.db, w.inv, iid, w.cause, "ราก", "ม", "")
    assert flow.record_investigation(w.db, w.inv, iid, w.cause, "ราก", "ม", None)
    assert flow.record_investigation(w.db, w.inv, iid, w.cause, "ราก", "ม", "0") == [] and w.row(iid)["lost_days"] == 0


def test_close_by_any_past_or_present_investigator_refused_even_for_admin(w):
    iid = w.new()
    flow.assign_investigator(w.db, w.admin, iid, w.admin["id"], "")                                   # แอดมินคนแรกเป็นผู้สอบสวน
    assert flow.record_investigation(w.db, w.admin, iid, w.cause, "ราก", "ม", "") == []
    assert flow.close(w.db, w.admin, iid, "ปิด")[0].startswith("ผู้ที่เคยเป็นผู้สอบสวน")
    w.db.execute("UPDATE incidents SET status='investigating' WHERE id=?", (iid,)); w.db.commit()   # โอนงานออกแล้วยังต้องห้าม
    flow.assign_investigator(w.db, w.admin2, iid, w.inv["id"], "")
    flow.record_investigation(w.db, w.inv, iid, w.cause, "ราก", "ม", "")
    assert flow.close(w.db, w.admin, iid, "ปิด")                                                      # เคยเป็นผู้สอบสวน (ประวัติ)
    assert flow.close(w.db, w.inv, iid, "ปิด")
    assert flow.close(w.db, w.admin2, iid, "ปิด") == [] and w.row(iid)["status"] == "closed"


def test_admin_who_recorded_the_investigation_cannot_close_it(w):
    iid = w.new()
    flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "")
    assert flow.record_investigation(w.db, w.admin2, iid, w.cause, "ราก", "มาตรการ", "") == []      # แอดมินบันทึกผลแทนผู้สอบสวน
    assert flow.close(w.db, w.admin2, iid, "ปิดเอง")[0].startswith("ผู้ที่เคยเป็นผู้สอบสวน")
    assert flow.close(w.db, w.admin, iid, "ปิดโดยอีกคน") == []


def test_close_needs_permission_status_and_note(w):
    iid = w.new()
    assert flow.close(w.db, w.admin, iid, "x")[0].startswith("สถานะ")
    w.to_actions_set(iid)
    assert flow.close(w.db, w.head, iid, "x") == ["คุณไม่มีสิทธิ์ปิดรายการอุบัติเหตุ"]                    # dept_head ไม่มี incident.close
    assert flow.close(w.db, w.admin, iid, "  ") and flow.close(w.db, w.admin, iid, "ก" * 1001)
    assert flow.close(w.db, w.inv_b, iid, "x") == flow.NOT_FOUND


def test_double_submit_close_creates_one_event_and_one_notification(w):
    iid = w.new()
    w.to_actions_set(iid)
    assert flow.close(w.db, w.admin, iid, "ปิด") == []
    assert flow.close(w.db, w.admin, iid, "ปิด") == ["สถานะของรายการนี้ไม่อนุญาตให้ทำรายการดังกล่าว"]   # ครั้งที่สอง: สถานะไม่อนุญาตแล้ว
    assert w.events(iid).count("closed") == 1 and len(w.notes("incident_closed")) == 1


def test_stale_version_loses_race_message_and_writes_nothing(w):
    iid = w.new()
    stale = w.row(iid)                                                                                  # อ่านไว้ก่อนมีคนมอบหมาย
    assert flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "") == []
    assert flow._transition(w.db, stale, {"owner_id": w.inv2["id"]}, "assigned", w.admin, "investigating") == flow.LOST_RACE
    r = w.row(iid)
    assert (r["owner_id"], r["version"]) == (w.inv["id"], 1) and w.events(iid) == ["reported", "assigned"]


def test_two_connections_race_assign_versus_reject_exactly_one_wins(tmp_path):
    path = str(tmp_path / "race.db")
    con = connect(path); ensure_schema(con)
    world = W(con)
    iid = world.new()
    results = {}

    def run(name, fn):
        c = connect(path)
        try:
            user = user_row(c, world.admin["id"])
            results[name] = fn(c, user)
        finally:
            c.close()

    barrier = threading.Barrier(2)
    def assign(c, u): barrier.wait(); return flow.assign_investigator(c, u, iid, world.inv["id"], "")
    def reject(c, u): barrier.wait(); return flow.reject(c, u, iid, "ซ้ำ")
    ts = [threading.Thread(target=run, args=("assign", assign)), threading.Thread(target=run, args=("reject", reject))]
    [t.start() for t in ts]; [t.join() for t in ts]
    wins = [k for k, v in results.items() if v == []]
    assert len(wins) == 1, results
    status = con.execute("SELECT status FROM incidents WHERE id=?", (iid,)).fetchone()[0]
    assert status == {"assign": "investigating", "reject": "rejected"}[wins[0]]
    assert con.execute("SELECT COUNT(*) FROM incident_events WHERE kind IN ('assigned','rejected')").fetchone()[0] == 1


def test_closed_and_rejected_are_terminal(w):
    iid = w.new()
    assert flow.reject(w.db, w.inv, iid, "ซ้ำซ้อน") == []
    for call in (lambda: flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], ""), lambda: flow.reject(w.db, w.inv, iid, "x"),
                 lambda: flow.update_people(w.db, w.inv, iid, {}), lambda: flow.close(w.db, w.admin2, iid, "x")):
        assert call()
    done = w.new()
    w.to_actions_set(done); flow.close(w.db, w.admin2, done, "ปิด")
    assert flow.reject(w.db, w.admin, done, "x") and flow.record_investigation(w.db, w.admin, done, w.cause, "ก", "ข", "1")


def test_reject_needs_reason_permission_and_notifies_nobody_extra(w):
    iid = w.new()
    assert flow.reject(w.db, w.rep, iid, "x") == ["คุณไม่มีสิทธิ์ปฏิเสธรายการ"]
    assert flow.reject(w.db, w.inv, iid, " ") and flow.reject(w.db, w.inv, iid, "ก" * 501)
    assert flow.reject(w.db, w.inv, iid, "ซ้ำ") == [] and w.row(iid)["reject_reason"] == "ซ้ำ"


def test_update_people_replaces_list_and_audit_has_count_only(w):
    iid = w.new(people_name_0="สมชาย ใจดี", people_no_0="EMP-7788")
    form = {"people_name_0": "สมหญิง รักดี", "people_role_0": "witness", "people_name_1": "สมศักดิ์", "people_no_1": "EMP-9"}
    v = w.row(iid)["version"]
    assert flow.update_people(w.db, w.inv, iid, form) == []
    names = [(p["name"], p["role"]) for p in w.db.execute("SELECT * FROM incident_people WHERE incident_id=? ORDER BY id", (iid,))]
    assert names == [("สมหญิง รักดี", "witness"), ("สมศักดิ์", "injured")] and w.row(iid)["version"] == v + 1
    ev = w.db.execute("SELECT * FROM incident_events WHERE kind='people_updated'").fetchone()
    assert json.loads(ev["data"]) == {"count": 2} and "สม" not in ev["note"] + ev["data"]
    assert flow.update_people(w.db, w.inv, iid, {"people_name_0": "ก" * 121})                          # ผิด: ไม่เปลี่ยนอะไร
    assert w.db.execute("SELECT COUNT(*) FROM incident_people WHERE incident_id=?", (iid,)).fetchone()[0] == 2
    assert flow.update_people(w.db, w.rep, iid, form) == ["คุณไม่มีสิทธิ์แก้ไขรายชื่อผู้เกี่ยวข้อง"]


def test_unseen_incident_returns_not_found_for_every_step(w):
    iid = w.new()
    other = w.inv_b
    for call in (lambda: flow.record_investigation(w.db, other, iid, w.cause, "a", "b", ""), lambda: flow.close(w.db, other, iid, "x"),
                 lambda: flow.reject(w.db, other, iid, "x"), lambda: flow.update_people(w.db, other, iid, {})):
        assert call() == flow.NOT_FOUND


def test_forms_with_a_stale_version_are_refused_for_people_and_investigation(w):
    iid = w.new(people_name_0="เดิม")
    seen = w.row(iid)["version"]
    assert flow.update_people(w.db, w.inv, iid, {"people_name_0": "คนแรกแก้", "version": str(seen)}) == []
    stale = flow.update_people(w.db, w.inv2, iid, {"people_name_0": "คนที่สองแก้ทับ", "version": str(seen)})
    assert stale == flow.STALE_FORM
    assert [p["name"] for p in w.db.execute("SELECT name FROM incident_people WHERE incident_id=?", (iid,))] == ["คนแรกแก้"]
    flow.assign_investigator(w.db, w.admin, iid, w.inv["id"], "")
    v = w.row(iid)["version"]
    assert flow.record_investigation(w.db, w.inv, iid, w.cause, "ก", "ข", "", expected_version=v) == []
    assert flow.record_investigation(w.db, w.admin, iid, w.cause, "ทับ", "ทับ", "", expected_version=v) == flow.STALE_FORM
    assert w.row(iid)["root_cause"] == "ก"
    assert flow.update_people(w.db, w.inv, iid, {"people_name_0": "ไม่ส่ง version"}) == []            # ไม่ส่ง version (เรียกจากโค้ด) ยังทำงานได้
