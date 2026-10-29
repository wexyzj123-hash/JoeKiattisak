import json
from datetime import date, timedelta

import pytest

from safety import timeutil, workflow
from safety.db import connect
from safety.risk import apply_criteria, criteria_form_values
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_refs, add_user, user_row

TODAY = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)


class W:
    """โลกจำลอง: โรงงาน A/B, ผู้ใช้หลายบทบาท"""
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        self.t, self.s = add_refs(db)
        ids = {
            "rep": add_user(db, "reporter", "ผู้แจ้ง"), "rep2": add_user(db, "reporter", "ผู้แจ้ง 2"),
            "asr": add_user(db, "assessor", "ผู้ประเมิน"), "asr2": add_user(db, "assessor", "ผู้ประเมิน 2"),
            "viewer": add_user(db, "viewer", "ผู้ดู"), "admin": add_user(db, "admin", "แอดมิน"),
            "scoped": add_user(db, "assessor", "ผู้ประเมินโรงงาน B", factory_id=self.b["f"]),
        }
        self.ids = ids

    def u(self, key):
        return user_row(self.db, self.ids[key])

    def form(self, **kw):
        return {"title": "น้ำมันหกพื้น", "description": "ลื่นล้ม", "risk_area_id": str(self.a["a"]),
                "hazard_type_id": str(self.t), "source_id": str(self.s), **kw}

    def hazard(self, who="rep", **kw):
        hid, errors = workflow.report(self.db, self.u(who), self.form(**kw))
        assert errors == [], errors
        return hid

    def row(self, hid):
        return self.db.execute("SELECT * FROM hazards WHERE id=?", (hid,)).fetchone()

    def events(self, hid):
        return [(e["kind"], e["from_status"], e["to_status"], e["actor_id"])
                for e in self.db.execute("SELECT * FROM hazard_events WHERE hazard_id=? ORDER BY id", (hid,))]

    def assessed(self, likelihood=3, severity=3, who="asr"):
        hid = self.hazard()
        assert workflow.assess(self.db, self.u(who), hid, likelihood, severity) == []
        return hid

    def assigned(self, likelihood=3, severity=3, owner="asr2", due=""):
        hid = self.assessed(likelihood, severity)
        assert workflow.assign(self.db, self.u("asr"), hid, self.ids[owner], due) == []
        return hid


@pytest.fixture
def w(tmp_path):
    db = connect(str(tmp_path / "s.db"))
    ensure_schema(db)
    return W(db)


# ---------- report ----------
def test_report_creates_hazard_event_and_code(w):
    hid = w.hazard()
    h = w.row(hid)
    assert (h["code"], h["status"], h["reported_by"], h["title"], h["score"]) == ("HZ-2603-0001", "reported", w.ids["rep"], "น้ำมันหกพื้น", None)
    assert w.events(hid) == [("reported", None, "reported", w.ids["rep"])]
    assert w.row(w.hazard())["code"] == "HZ-2603-0002"


@pytest.mark.parametrize("bad", [
    {"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"description": "x" * 2001},
    {"risk_area_id": "999"}, {"risk_area_id": "abc"}, {"risk_area_id": ""}, {"hazard_type_id": "999"},
    {"source_id": "999"}, {"source_id": ""}])
def test_report_validation(w, bad):
    hid, errors = workflow.report(w.db, w.u("rep"), w.form(**bad))
    assert hid is None and errors and w.db.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 0


def test_report_rejects_inactive_refs_and_inactive_ancestor_and_out_of_scope(w):
    w.db.execute("UPDATE hazard_types SET active=0"); w.db.commit()
    assert workflow.report(w.db, w.u("rep"), w.form())[0] is None
    w.db.execute("UPDATE hazard_types SET active=1"); w.db.execute("UPDATE hazard_sources SET active=0"); w.db.commit()
    assert workflow.report(w.db, w.u("rep"), w.form())[0] is None
    w.db.execute("UPDATE hazard_sources SET active=1"); w.db.execute("UPDATE factories SET active=0 WHERE id=?", (w.a["f"],)); w.db.commit()
    assert workflow.report(w.db, w.u("rep"), w.form())[0] is None                      # โรงงานของพื้นที่ปิดอยู่
    w.db.execute("UPDATE factories SET active=1"); w.db.commit()
    assert workflow.report(w.db, w.u("scoped"), w.form())[0] is None                   # พื้นที่ของโรงงาน A แต่ผู้ใช้ขอบเขตโรงงาน B


def test_report_requires_permission(w):
    hid, errors = workflow.report(w.db, w.u("viewer"), w.form())
    assert hid is None and any("สิทธิ์" in e for e in errors)


def test_report_validates_custom_fields_and_stores_json(w):
    w.db.execute("INSERT INTO custom_fields(hazard_type_id,key,label_th,type,required) VALUES(?,?,?,?,1)", (w.t, "temp", "อุณหภูมิ", "number"))
    w.db.commit()
    hid, errors = workflow.report(w.db, w.u("rep"), w.form(cf_temp="abc"))
    assert hid is None and any("อุณหภูมิ" in e for e in errors)
    hid, errors = workflow.report(w.db, w.u("rep"), w.form(cf_temp="85.5"))
    assert errors == [] and json.loads(w.row(hid)["custom"]) == {"temp": 85.5}


# ---------- assess ----------
def test_assess_snapshots_score_and_level(w):
    hid = w.assessed(4, 4)
    h = w.row(hid)
    assert (h["status"], h["likelihood"], h["severity"], h["score"], h["level_name"], h["level_color"], h["level_requires_approval"]) == \
        ("assessed", 4, 4, 16, "สูง", "#ff9f1c", 0)
    assert h["risk_level_id"] is not None
    assert w.events(hid)[-1] == ("assessed", "reported", "assessed", w.ids["asr"])


@pytest.mark.parametrize("l, s, name, score", [(1, 4, "ต่ำ", 4), (1, 5, "ปานกลาง", 5), (5, 5, "สูงมาก", 25), (3, 3, "ปานกลาง", 9), (2, 5, "สูง", 10)])
def test_assess_boundaries(w, l, s, name, score):
    h = w.row(w.assessed(l, s))
    assert (h["score"], h["level_name"]) == (score, name)
    assert h["level_requires_approval"] == (1 if name == "สูงมาก" else 0)


@pytest.mark.parametrize("l, s", [(0, 3), (6, 3), (3, 0), (3, 6), (-1, 2), ("3", 3), (True, 3), (None, 3)])
def test_assess_rejects_out_of_range_and_non_int(w, l, s):
    hid = w.hazard()
    assert workflow.assess(w.db, w.u("asr"), hid, l, s)
    assert w.row(hid)["status"] == "reported" and len(w.events(hid)) == 1


def test_reassess_before_assign_logs_old_and_new(w):
    hid = w.assessed(2, 2)
    assert workflow.assess(w.db, w.u("asr2"), hid, 5, 5, note="ประเมินใหม่") == []
    h = w.row(hid)
    assert (h["score"], h["level_name"], h["status"]) == (25, "สูงมาก", "assessed")
    ev = w.db.execute("SELECT * FROM hazard_events WHERE hazard_id=? ORDER BY id DESC LIMIT 1", (hid,)).fetchone()
    data = json.loads(ev["data"])
    assert ev["note"] == "ประเมินใหม่" and data["old"]["score"] == 4 and data["new"]["score"] == 25


def test_cannot_assess_after_assign_or_close_or_reject(w):
    hid = w.assigned()
    assert workflow.assess(w.db, w.u("asr"), hid, 1, 1) and w.row(hid)["score"] == 9
    workflow.close(w.db, w.u("asr"), hid, "แก้แล้ว")
    assert workflow.assess(w.db, w.u("asr"), hid, 1, 1)
    h2 = w.hazard(); workflow.reject(w.db, w.u("asr"), h2, "ไม่ใช่อันตราย")
    assert workflow.assess(w.db, w.u("asr"), h2, 1, 1)


# ---------- assign ----------
def test_assign_default_due_from_sla(w):
    hid = w.assigned(5, 5)                                   # สูงมาก SLA 3 วัน
    assert w.row(hid)["due_date"] == str(TODAY + timedelta(days=3))
    low = w.assigned(1, 1)                                   # ต่ำ SLA 30 วัน
    assert w.row(low)["due_date"] == str(TODAY + timedelta(days=30))
    h = w.row(low)
    assert (h["status"], h["owner_id"]) == ("assigned", w.ids["asr2"])


@pytest.mark.parametrize("due, ok", [
    (str(TODAY), True), (str(TODAY + timedelta(days=365)), True), (str(TODAY + timedelta(days=1)), True),
    (str(TODAY - timedelta(days=1)), False), (str(TODAY + timedelta(days=366)), False),
    ("2026-02-30", False), ("abc", False), ("10/03/2026", False), ("2026-3-10", False), ("2026-03-10 00:00", False)])
def test_assign_due_date_validation(w, due, ok):
    hid = w.assessed()
    errors = workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], due)
    assert (errors == []) is ok, errors
    assert w.row(hid)["status"] == ("assigned" if ok else "assessed")


@pytest.mark.parametrize("owner", [None, "", "abc", 999, "inactive"])
def test_assign_owner_must_be_active_user(w, owner):
    if owner == "inactive":
        owner = add_user(w.db, "reporter", "ปิด", active=0)
    hid = w.assessed()
    assert workflow.assign(w.db, w.u("asr"), hid, owner, "")
    assert w.row(hid)["status"] == "assessed"


def test_assign_requires_assessment_first(w):
    hid = w.hazard()
    assert workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], "") and w.row(hid)["status"] == "reported"


def test_reassign_changes_owner_and_logs(w):
    hid = w.assigned()
    assert workflow.assign(w.db, w.u("asr"), hid, w.ids["rep"], str(TODAY + timedelta(days=5)), note="โอนงาน") == []
    h = w.row(hid)
    assert (h["owner_id"], h["due_date"], h["status"]) == (w.ids["rep"], str(TODAY + timedelta(days=5)), "assigned")
    ev = w.db.execute("SELECT * FROM hazard_events WHERE hazard_id=? ORDER BY id DESC LIMIT 1", (hid,)).fetchone()
    assert json.loads(ev["data"])["old_owner"] == w.ids["asr2"] and ev["note"] == "โอนงาน"


# ---------- close ----------
def test_close_requires_note_and_assigned_state(w):
    hid = w.assessed()
    assert workflow.close(w.db, w.u("asr"), hid, "แก้แล้ว")                              # ยังไม่ assigned
    hid = w.assigned()
    assert workflow.close(w.db, w.u("asr"), hid, "  ") and workflow.close(w.db, w.u("asr"), hid, "x" * 1001)
    assert workflow.close(w.db, w.u("asr"), hid, "ทำความสะอาดแล้ว") == []
    h = w.row(hid)
    assert (h["status"], h["closing_note"]) == ("closed", "ทำความสะอาดแล้ว")
    assert w.events(hid)[-1] == ("closed", "assigned", "closed", w.ids["asr"])
    assert workflow.close(w.db, w.u("asr"), hid, "อีกครั้ง")                              # ปิดซ้ำไม่ได้


def test_close_by_owner_blocked_when_level_requires_approval_but_allowed_otherwise(w):
    high = w.assigned(5, 5, owner="asr2")
    assert any("ผู้รับผิดชอบ" in e for e in workflow.close(w.db, w.u("asr2"), high, "แก้เอง"))
    assert w.row(high)["status"] == "assigned"
    assert workflow.close(w.db, w.u("asr"), high, "ตรวจแล้ว") == []                      # คนอื่นปิดได้
    low = w.assigned(1, 1, owner="asr2")
    assert workflow.close(w.db, w.u("asr2"), low, "แก้เอง") == []                        # ระดับต่ำ เจ้าของปิดเองได้


def test_four_eyes_rule_uses_snapshot_not_current_criteria(w):
    hid = w.assigned(5, 5, owner="asr2")
    f = criteria_form_values(w.db)
    last = int(f["level_count"]) - 4                                                      # ระดับสุดท้ายที่มีอยู่ = "สูงมาก"
    f[f"level_approval_{last}"] = ""
    assert apply_criteria(w.db, f) == []
    assert workflow.close(w.db, w.u("asr2"), hid, "แก้เอง")                               # ยังต้องใช้คนอื่นตาม snapshot


# ---------- reject ----------
@pytest.mark.parametrize("stage", ["reported", "assessed", "assigned"])
def test_reject_from_each_open_state(w, stage):
    hid = {"reported": w.hazard, "assessed": w.assessed, "assigned": w.assigned}[stage]()
    assert workflow.reject(w.db, w.u("asr"), hid, "ซ้ำกับรายการอื่น") == []
    h = w.row(hid)
    assert (h["status"], h["reject_reason"]) == ("rejected", "ซ้ำกับรายการอื่น")
    assert w.events(hid)[-1][:3] == ("rejected", stage, "rejected")


def test_reject_needs_reason_and_terminal_states_locked(w):
    hid = w.hazard()
    assert workflow.reject(w.db, w.u("asr"), hid, " ") and workflow.reject(w.db, w.u("asr"), hid, "x" * 1001)
    workflow.reject(w.db, w.u("asr"), hid, "ไม่ใช่อันตราย")
    for fn in (lambda: workflow.reject(w.db, w.u("asr"), hid, "อีกที"), lambda: workflow.assess(w.db, w.u("asr"), hid, 1, 1),
               lambda: workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], ""), lambda: workflow.close(w.db, w.u("asr"), hid, "x")):
        assert fn()
    closed = w.assigned(); workflow.close(w.db, w.u("asr"), closed, "ok")
    assert workflow.reject(w.db, w.u("asr"), closed, "ปฏิเสธหลังปิด")


# ---------- permissions / scope / races / audit ----------
def test_permissions_enforced_per_action(w):
    hid = w.hazard()
    nobody = w.u("rep")                                                                  # reporter: hazard.report เท่านั้น
    assert workflow.assess(w.db, nobody, hid, 1, 1) and workflow.reject(w.db, nobody, hid, "x")
    ok = w.assessed()
    assert workflow.assign(w.db, nobody, ok, w.ids["asr"], "") and workflow.close(w.db, nobody, ok, "x")
    assert workflow.assess(w.db, w.u("viewer"), hid, 1, 1)
    assert w.row(hid)["status"] == "reported" and w.row(ok)["status"] == "assessed"


def test_out_of_scope_hazard_is_not_found_for_every_action(w):
    hid = w.assigned()                                                                    # พื้นที่โรงงาน A
    scoped = w.u("scoped")                                                                # ผู้ประเมินขอบเขตโรงงาน B
    results = [workflow.assess(w.db, scoped, hid, 1, 1), workflow.assign(w.db, scoped, hid, w.ids["asr"], ""),
               workflow.close(w.db, scoped, hid, "x"), workflow.reject(w.db, scoped, hid, "x")]
    assert all(r == ["ไม่พบรายการ"] for r in results)
    assert w.row(hid)["status"] == "assigned"
    assert workflow.assess(w.db, w.u("asr"), 999999, 1, 1) == ["ไม่พบรายการ"]


def test_reporter_cannot_act_on_others_hazard_even_with_role_perms(w):
    hid = w.hazard("rep")
    other_reporter_with_assess = add_user(w.db, "assessor", "x")                          # assessor เห็นทั้งหมดอยู่แล้ว
    assert workflow.assess(w.db, user_row(w.db, other_reporter_with_assess), hid, 1, 1) == []


@pytest.mark.parametrize("action", ["assess", "assign", "close", "reject"])
def test_lost_race_second_transition_fails_without_event(w, monkeypatch, action):
    hid = {"assess": w.hazard, "assign": w.assessed, "close": w.assigned, "reject": w.hazard}[action]()
    before = len(w.events(hid))
    real = workflow.visible_hazard

    def racing(db, user, hazard_id):
        row = real(db, user, hazard_id)
        db.execute("UPDATE hazards SET status='closed' WHERE id=?", (hazard_id,))       # อีกคนเปลี่ยนสถานะหลังเราอ่านแล้ว
        db.commit()
        return row

    monkeypatch.setattr(workflow, "visible_hazard", racing)
    call = {"assess": lambda: workflow.assess(w.db, w.u("asr"), hid, 2, 2),
            "assign": lambda: workflow.assign(w.db, w.u("asr"), hid, w.ids["asr2"], ""),
            "close": lambda: workflow.close(w.db, w.u("asr"), hid, "ปิดแล้ว"),
            "reject": lambda: workflow.reject(w.db, w.u("asr"), hid, "ไม่ใช่")}[action]
    errors = call()
    assert errors and w.row(hid)["status"] == "closed" and len(w.events(hid)) == before


def test_two_assessors_first_assessment_exactly_one_wins(w, monkeypatch):
    hid = w.hazard()
    real = workflow.visible_hazard
    state = {"first": True}

    def racing(db, user, hazard_id):
        row = real(db, user, hazard_id)
        if state["first"]:                                                                # ผู้ประเมินคนที่ 2 ตัดหน้าหลังคนแรกอ่านสถานะแล้ว
            state["first"] = False
            assert workflow.assess(db, w.u("asr2"), hazard_id, 5, 5) == []
        return row

    monkeypatch.setattr(workflow, "visible_hazard", racing)
    assert workflow.assess(w.db, w.u("asr"), hid, 1, 1)                                   # คนแรกแพ้ (สถานะไม่ใช่ reported แล้ว)
    assert w.row(hid)["score"] == 25
    assert [e for e in w.events(hid) if e[0] == "assessed"] == [("assessed", "reported", "assessed", w.ids["asr2"])]


def test_each_success_writes_exactly_one_event_with_actor(w):
    hid = w.assigned()
    workflow.close(w.db, w.u("asr"), hid, "เสร็จ")
    assert w.events(hid) == [("reported", None, "reported", w.ids["rep"]), ("assessed", "reported", "assessed", w.ids["asr"]),
                             ("assigned", "assessed", "assigned", w.ids["asr"]), ("closed", "assigned", "closed", w.ids["asr"])]


def test_failed_actions_write_no_events(w):
    hid = w.hazard()
    workflow.assess(w.db, w.u("asr"), hid, 9, 9); workflow.assign(w.db, w.u("asr"), hid, w.ids["asr"], "")
    workflow.close(w.db, w.u("asr"), hid, "x"); workflow.reject(w.db, w.u("asr"), hid, "")
    assert len(w.events(hid)) == 1


def test_criteria_edit_after_assessment_leaves_snapshot(w):
    hid = w.assessed(5, 5)
    f = criteria_form_values(w.db)
    f["level_name_3"], f["level_color_3"] = "วิกฤต", "#000000"
    assert apply_criteria(w.db, f) == []
    h = w.row(hid)
    assert (h["level_name"], h["level_color"], h["score"]) == ("สูงมาก", "#ff4d4f", 25)
