"""สายอนุมัติ 2 ขั้น (SV → DM/DGM) แยกตามหน่วยงาน + ประกาศทางการถึงทุกคนในหน่วยงานและ GM"""
import pytest

from safety import approvals, incidents, timeutil, workflow
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_refs, add_user, user_row
from tests.safety.helpers import login


class World:
    def __init__(self, app):
        self.app = app
        self.db = connect(app.config["DATABASE"])
        self.a, self.b = add_chain(self.db, "A"), add_chain(self.db, "B")
        self.t, self.s = add_refs(self.db)
        pos = lambda code: self.db.execute("SELECT id FROM positions WHERE code=?", (code,)).fetchone()[0]
        mk = lambda role, name, code, chain=None, factory_only=False: self._user(role, name, pos(code) if code else None, chain, factory_only)
        self.rep = mk("reporter", "ผู้แจ้ง", "OPERATOR", self.a)
        self.mate = mk("viewer", "เพื่อนร่วมแผนก", "STAFF", self.a)
        self.other = mk("viewer", "คนแผนกอื่น", "STAFF", self.b)
        self.sv = mk("reporter", "หัวหน้า SV", "SV", self.a)
        self.dm = mk("reporter", "ผู้จัดการ DM", "DM", self.a)
        self.dgm = mk("reporter", "DGM โรงงาน", "DGM", self.a, factory_only=True)
        self.gm = mk("viewer", "GM ทั้งบริษัท", "GM")
        self.sv_b = mk("reporter", "SV แผนก B", "SV", self.b)
        self.admin = add_user(self.db, "admin", "แอดมิน")
        self.db.execute("UPDATE safety_users SET email=? WHERE id=?", ("x@example.com", self.sv))
        self.db.commit()

    def _user(self, role, name, position_id, chain, factory_only):
        uid = add_user(self.db, role, name, factory_id=chain["f"] if chain else None,
                       department_id=None if (factory_only or not chain) else chain["d"])
        self.db.execute("UPDATE safety_users SET position_id=?, notify_channels='inapp' WHERE id=?", (position_id, uid))
        self.db.commit()
        return uid

    def on(self):
        approvals.save_config(self.db, True, ["SV"], ["DM", "DGM"], ["GM"])

    def hazard(self, chain=None):
        hid, errors = workflow.report(self.db, user_row(self.db, self.rep), {
            "title": "น้ำมันหก", "risk_area_id": str((chain or self.a)["a"]), "hazard_type_id": str(self.t), "source_id": str(self.s)})
        assert errors == [], errors
        return hid

    def notes(self, event, hid=None):
        q = "SELECT u.name FROM notifications n JOIN safety_users u ON u.id=n.user_id WHERE n.event=?"
        args = [event]
        if hid is not None:
            q += " AND n.hazard_id=?"; args.append(hid)
        return sorted(r[0] for r in self.db.execute(q + " ORDER BY u.name", args))


@pytest.fixture
def w(safety_app):
    return World(safety_app)


def decide(w, who, hid, step, approve=True, comment="", kind="hazard"):
    return approvals.decide(w.db, user_row(w.db, who), kind, hid, step, approve, comment)


def test_disabled_by_default_creates_nothing_and_does_not_block(w):
    hid = w.hazard()
    assert w.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert approvals.gate_errors(w.db, "hazard", hid) == []


def test_two_step_chain_in_order_and_gate_on_close(w):
    w.on()
    hid = w.hazard()
    assert [r["step"] for r in approvals.rows(w.db, "hazard", hid)] == [1, 2]
    assert approvals.gate_errors(w.db, "hazard", hid)                                      # ยังปิดไม่ได้
    assert decide(w, w.dm, hid, 2)                                                           # ข้ามขั้นไม่ได้ (ขั้น 1 ยังไม่เสร็จ)
    assert decide(w, w.dm, hid, 1)                                                           # DM ไม่ใช่ตำแหน่งของขั้น 1
    assert decide(w, w.sv_b, hid, 1)                                                         # SV คนละแผนก
    assert decide(w, w.rep, hid, 1)                                                          # ผู้แจ้งเอง/ตำแหน่งไม่ตรง
    assert decide(w, w.sv, hid, 1) == []
    assert "ขั้น 2" in approvals.gate_errors(w.db, "hazard", hid)[0] and "ขั้น 1" not in approvals.gate_errors(w.db, "hazard", hid)[0]
    assert decide(w, w.dgm, hid, 2) == []                                                    # DGM ระดับโรงงานแทน DM ได้
    assert approvals.gate_errors(w.db, "hazard", hid) == []
    assert w.db.execute("SELECT COUNT(*) FROM hazard_events WHERE hazard_id=? AND kind='approval'", (hid,)).fetchone()[0] == 2


def test_close_blocked_until_approved_then_allowed(w):
    w.on()
    hid = w.hazard()
    asr = add_user(w.db, "assessor", "ผู้ประเมิน", factory_id=w.a["f"])
    user = user_row(w.db, asr)
    assert workflow.assess(w.db, user, hid, 2, 2, "", 2) == []
    assert workflow.assign(w.db, user, hid, asr, "") == []
    errors = workflow.close(w.db, user_row(w.db, add_user(w.db, "assessor", "คนปิด", factory_id=w.a["f"])), hid, "แก้แล้ว")
    assert errors and "ต้องได้รับอนุมัติครบ" in errors[0]
    decide(w, w.sv, hid, 1); decide(w, w.dm, hid, 2)
    closer = user_row(w.db, add_user(w.db, "assessor", "คนปิด2", factory_id=w.a["f"]))
    assert workflow.close(w.db, closer, hid, "แก้แล้ว") == []


def test_reject_needs_reason_then_blocks_and_restart_resets(w):
    w.on()
    hid = w.hazard()
    assert decide(w, w.sv, hid, 1, approve=False)                                            # ไม่ระบุเหตุผล
    assert decide(w, w.sv, hid, 1, approve=False, comment="ข้อมูลไม่ครบ") == []
    assert approvals.summary(w.db, "hazard", hid)["state"] == "rejected"
    assert "ไม่ได้รับอนุมัติ" in approvals.gate_errors(w.db, "hazard", hid)[0]
    assert "ข้อมูลไม่ครบ" in " ".join(r[0] for r in w.db.execute("SELECT body FROM notifications WHERE event='approval_rejected'"))
    assert approvals.restart(w.db, user_row(w.db, w.sv), "hazard", hid)                      # SV ไม่มีสิทธิ์ขอใหม่
    assert approvals.restart(w.db, user_row(w.db, w.admin), "hazard", hid) == []
    assert approvals.summary(w.db, "hazard", hid)["state"] == "pending"


def test_admin_proxy_needs_reason_and_is_recorded(w):
    w.on()
    hid = w.hazard()
    assert decide(w, w.admin, hid, 1)                                                        # อนุมัติแทนต้องมีเหตุผล
    assert decide(w, w.admin, hid, 1, comment="ยังไม่มี SV ในหน่วยงาน") == []
    row = approvals.rows(w.db, "hazard", hid)[0]
    assert row["proxy"] == 1 and row["approver_id"] == w.admin


def test_queue_page_lists_only_my_step_and_decides_via_http(w):
    w.on()
    hid = w.hazard()
    assert approvals.pending_for(w.db, user_row(w.db, w.dm)) == []                           # ยังไม่ถึงขั้น 2
    from tests.safety.helpers import login_as
    c = login_as(w.app, w.sv)
    page = c.get("/approvals").get_data(as_text=True)
    assert "น้ำมันหก" in page and "ขั้นที่ 1" in page
    r = c.post(f"/approvals/hazard/{hid}/1", data={"_csrf": csrf(c), "decision": "approve", "comment": "เห็นชอบ"})
    assert r.status_code == 302
    assert approvals.summary(w.db, "hazard", hid)["rows"][0]["status"] == "approved"
    assert "น้ำมันหก" not in c.get("/approvals").get_data(as_text=True).split("การตัดสินใจล่าสุด")[0]
    other = login_as(w.app, w.sv_b)
    assert other.post(f"/approvals/hazard/{hid}/2", data={"_csrf": csrf(other), "decision": "approve"}).status_code == 302
    assert approvals.summary(w.db, "hazard", hid)["next_step"] == 2                          # ไม่ผ่านเพราะไม่มีสิทธิ์
    assert c.post(f"/approvals/hazard/{hid}/1", data={"decision": "approve"}).status_code == 400          # ไม่มี CSRF


def test_unit_notice_goes_to_everyone_in_unit_and_gm_with_formal_text(w):
    hid = w.hazard()
    got = w.notes("unit_hazard_notice", hid)
    assert "เพื่อนร่วมแผนก" in got and "หัวหน้า SV" in got and "ผู้จัดการ DM" in got and "GM ทั้งบริษัท" in got and "DGM โรงงาน" in got
    assert "คนแผนกอื่น" not in got and "SV แผนก B" not in got                                # แผนกอื่นไม่ได้รับ
    assert "ผู้แจ้ง" not in got                                                                 # ผู้แจ้งเองไม่ต้องได้รับ
    body = w.db.execute("SELECT body FROM notifications WHERE event='unit_hazard_notice' LIMIT 1").fetchone()[0]
    assert body.startswith("เรียน ผู้เกี่ยวข้อง") and "จึงเรียนมาเพื่อโปรดทราบ" in body and "น้ำมันหก" in body


def test_unit_notice_for_incident_and_no_duplicate_for_people_already_notified(w):
    asr = add_user(w.db, "assessor", "ผู้ประเมินแผนก", factory_id=w.a["f"], department_id=w.a["d"])
    w.db.execute("UPDATE safety_users SET notify_channels='inapp' WHERE id=?", (asr,)); w.db.commit()
    iid, errors = incidents.report(w.db, user_row(w.db, w.rep), {
        "kind": "accident", "occurred_at": timeutil.now().strftime("%Y-%m-%dT%H:%M"), "risk_area_id": str(w.a["a"]), "title": "รถตักชนเสา",
        "injury_severity_id": str(w.db.execute("SELECT id FROM injury_severities LIMIT 1").fetchone()[0])})
    assert errors == []
    names = [r[0] for r in w.db.execute("SELECT u.name FROM notifications n JOIN safety_users u ON u.id=n.user_id WHERE n.incident_id=? AND n.event='unit_incident_notice'", (iid,))]
    assert "เพื่อนร่วมแผนก" in names and "GM ทั้งบริษัท" in names and "คนแผนกอื่น" not in names
    assert "ผู้ประเมินแผนก" not in names                                                         # ได้ 'แจ้งอุบัติเหตุใหม่' จากกฎเดิมแล้ว ไม่ซ้ำ
    assert w.db.execute("SELECT COUNT(*) FROM notifications WHERE incident_id=? AND event='incident_reported' AND user_id=?", (iid, asr)).fetchone()[0] == 1


def test_request_notifies_step_approvers_then_completion_notifies_gm_and_reporter(w):
    w.on()
    hid = w.hazard()
    assert w.notes("approval_requested", hid) == ["หัวหน้า SV"]
    decide(w, w.sv, hid, 1)
    assert w.notes("approval_requested", hid) == sorted(["DGM โรงงาน", "หัวหน้า SV", "ผู้จัดการ DM"])
    decide(w, w.dm, hid, 2)
    assert w.notes("approval_completed", hid) == sorted(["GM ทั้งบริษัท", "ผู้แจ้ง"])


def test_admin_page_config_and_org_matrix(w):
    from tests.safety.helpers import login_as
    c = login_as(w.app, w.admin)
    html = c.get("/admin/approvals").get_data(as_text=True)
    assert "หัวหน้า SV" in html and "GM ทั้งบริษัท" in html and "แผนก A" in html
    assert c.post("/admin/approvals", data={"_csrf": csrf(c), "enabled": "1", "step1": ["SV"], "step2": ["DM"], "notify": ["GM"]}).status_code == 302
    assert approvals.enabled(w.db) and approvals.step_codes(w.db, 2) == ["DM"]
    c.post("/admin/approvals", data={"_csrf": csrf(c), "enabled": "1", "step1": [], "step2": ["DM"]})
    assert approvals.step_codes(w.db, 1) == ["SV"]                                          # ว่างไม่ได้ → ไม่เปลี่ยน
    c.post("/admin/approvals", data={"_csrf": csrf(c), "enabled": "1", "step1": ["NOPE"], "step2": ["DM"]})
    assert approvals.step_codes(w.db, 1) == ["SV"]
    assert login_as(w.app, w.sv).get("/admin/approvals").status_code == 403
    m = {g["label"]: g for g in approvals.org_matrix(w.db)}
    a = m["โรงงาน A › แผนก A"]
    assert [u["code"] for u in a["users"]] == ["SV", "DM", "DGM", "GM"] and a["missing"] == []              # เรียงต่ำ→สูง
    assert m["โรงงาน B › แผนก B"]["missing"] and "ขั้น 2" in m["โรงงาน B › แผนก B"]["missing"][0]        # แผนก B มี SV แต่ยังไม่มี DM/DGM


def test_rules_accept_unit_position_and_approvers_tokens(w):
    from tests.safety.helpers import login_as
    c = login_as(w.app, w.admin)
    r = c.post("/admin/notifications", data={"_csrf": csrf(c), "action": "rule_create", "event": "hazard_closed", "recipients": "unit,position:GM,approvers",
                                              "channels": ["inapp"], "offset_days": "0", "min_score": ""})
    assert r.status_code == 302
    assert w.db.execute("SELECT 1 FROM notify_rules WHERE event='hazard_closed' AND recipients='unit,position:GM,approvers'").fetchone()
