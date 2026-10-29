import io
import threading
from datetime import datetime, timedelta

import pytest
from PIL import Image

from safety import capa, incident_flow, incidents, patrol, timeutil, workflow
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_refs, add_user, severity_id, user_row

NOW = datetime(2026, 3, 10, 14, 30)


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
    monkeypatch.setattr(timeutil, "today", lambda: NOW.date())


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def png():
    buf = io.BytesIO(); Image.new("RGB", (30, 30), (9, 9, 9)).save(buf, "PNG"); return buf.getvalue()


class W:
    def __init__(self, db, tmp_path):
        self.db, self.root = db, str(tmp_path / "up")
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        u = lambda role, name, **kw: user_row(db, add_user(db, role, name, **kw))
        self.insp = u("assessor", "ผู้ตรวจ")
        db.execute("INSERT INTO safety_roles(key,name_th,permissions) VALUES('patrol_only','ผู้ตรวจล้วน','patrol.perform')")
        self.plain = u("patrol_only", "ผู้ตรวจที่ไม่มีสิทธิ์อื่น")                 # ไม่มี hazard.report / capa.create — ต้องสร้างข้อค้นพบได้
        self.insp_b = u("assessor", "ผู้ตรวจ B", factory_id=self.b["f"])
        self.verifier, self.owner = u("assessor", "ผู้ยืนยัน"), u("reporter", "เจ้าของ")
        self.admin, self.admin2 = u("admin", "แอดมิน"), u("admin", "แอดมิน 2")
        self.type_id, self.source_id = add_refs(db)
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','ตรวจ')").lastrowid
        for n in (1, 2):
            db.execute("INSERT INTO checklist_items(checklist_id, section, text_th, require_photo_on_fail, sort) VALUES(?,?,?,?,?)", (self.cl, "หมวด", f"ข้อ {n}", n == 2, n))
        db.commit()

    def submitted_round(self, user=None, results=("fail", "fail"), photos=True, chain="a"):
        user = user or self.insp
        rid, errors = patrol.start_round(self.db, user, checklist_id=self.cl, risk_area_id=getattr(self, chain)["a"])
        assert errors == [], errors
        answers = self.db.execute("SELECT * FROM patrol_answers WHERE round_id=? ORDER BY sort", (rid,)).fetchall()
        assert patrol.save_answers(self.db, user, rid, {**{f"result_{a['id']}": res for a, res in zip(answers, results)},
                                                          f"note_{answers[0]['id']}": "สายไฟชำรุดตรงเสา"}) == []
        if photos:
            assert patrol.add_photo(self.db, user, rid, answers[1]["id"], "a.png", png(), self.root) == []
        assert patrol.submit_round(self.db, user, rid) == [], results
        return rid, [a["id"] for a in answers]

    def capa_form(self, **kw):
        return {"title": "แก้สายไฟ", "owner_id": str(self.owner["id"]), "due_date": (NOW.date() + timedelta(days=7)).isoformat(), "action_type": "corrective", **kw}


@pytest.fixture
def w(db, tmp_path):
    return W(db, tmp_path)


# ---------- ข้อค้นพบ → รายการอันตราย ----------
def test_finding_to_hazard_creates_one_linked_hazard_with_photos_even_without_hazard_report_permission(w):
    rid, (a1, a2) = w.submitted_round(w.plain)
    hid, errors = patrol.finding_to_hazard(w.db, w.plain, a2, w.type_id, w.source_id, w.root)
    assert errors == [] and hid
    h = w.db.execute("SELECT * FROM hazards WHERE id=?", (hid,)).fetchone()
    assert (h["title"], h["patrol_answer_id"], h["risk_area_id"], h["reported_by"], h["status"]) == ("ข้อ 2", a2, w.a["a"], w.plain["id"], "reported")
    assert w.db.execute("SELECT COUNT(*) FROM hazard_attachments WHERE hazard_id=? AND kind='before'", (hid,)).fetchone()[0] == 1
    assert patrol.finding_to_hazard(w.db, w.plain, a2, w.type_id, w.source_id, w.root) == (hid, [])           # กดซ้ำได้ตัวเดิม
    assert w.db.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 1
    h1, _ = patrol.finding_to_hazard(w.db, w.plain, a1, w.type_id, w.source_id, w.root)
    assert "สายไฟชำรุดตรงเสา" in w.db.execute("SELECT description FROM hazards WHERE id=?", (h1,)).fetchone()[0]
    assert w.db.execute("SELECT COUNT(*) FROM notifications WHERE event='hazard_reported'").fetchone()[0] >= 1


def test_finding_to_hazard_two_connections_make_exactly_one(tmp_path):
    path = str(tmp_path / "race.db")
    con = connect(path); ensure_schema(con)
    world = W(con, tmp_path)
    rid, (a1, a2) = world.submitted_round()
    out, barrier = [], threading.Barrier(2)

    def run():
        c = connect(path)
        try:
            barrier.wait()
            out.append(patrol.finding_to_hazard(c, user_row(c, world.insp["id"]), a2, world.type_id, world.source_id, world.root))
        finally:
            c.close()
    ts = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert con.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 1
    assert {o[0] for o in out} == {con.execute("SELECT id FROM hazards").fetchone()[0]} and all(o[1] == [] for o in out)


def test_finding_to_hazard_refusals(w):
    _, (a1, a2) = w.submitted_round(results=("pass", "fail"))
    assert patrol.finding_to_hazard(w.db, w.insp, a1, w.type_id, w.source_id, w.root)[0] is None             # ข้อที่ผ่าน
    draft, errors = patrol.start_round(w.db, w.admin, checklist_id=w.cl, risk_area_id=w.b["a"])
    draft_answer = w.db.execute("SELECT id FROM patrol_answers WHERE round_id=?", (draft,)).fetchone()[0]
    w.db.execute("UPDATE patrol_answers SET result='fail' WHERE id=?", (draft_answer,)); w.db.commit()
    assert patrol.finding_to_hazard(w.db, w.admin, draft_answer, w.type_id, w.source_id, w.root)[0] is None   # ร่างยังไม่ส่ง
    assert patrol.finding_to_hazard(w.db, w.insp_b, a2, w.type_id, w.source_id, w.root) == (None, patrol.NOT_FOUND)  # คนละโรงงาน
    assert patrol.finding_to_hazard(w.db, w.owner, a2, w.type_id, w.source_id, w.root)[0] is None
    for t, s in ((9999, w.source_id), (w.type_id, 9999), ("x", w.source_id), (None, None)):
        assert patrol.finding_to_hazard(w.db, w.insp, a2, t, s, w.root)[0] is None
    w.db.execute("UPDATE hazard_types SET active=0"); w.db.commit()
    assert patrol.finding_to_hazard(w.db, w.insp, a2, w.type_id, w.source_id, w.root)[0] is None
    assert patrol.finding_to_hazard(w.db, w.insp, 999999, w.type_id, w.source_id, w.root) == (None, patrol.NOT_FOUND)
    assert w.db.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 0


# ---------- ข้อค้นพบ → CAPA ----------
def test_finding_to_capa_idempotent_linked_and_needs_only_patrol_perform(w):
    _, (a1, a2) = w.submitted_round(w.plain)
    cid, errors = patrol.finding_to_capa(w.db, w.plain, a1, w.capa_form())
    assert errors == [] and cid
    row = w.db.execute("SELECT * FROM capas WHERE id=?", (cid,)).fetchone()
    assert (row["source_kind"], row["source_id"], row["risk_area_id"], row["created_by"], row["owner_id"]) == ("patrol", a1, w.a["a"], w.plain["id"], w.owner["id"])
    assert patrol.finding_to_capa(w.db, w.plain, a1, w.capa_form(title="ซ้ำ")) == (cid, [])
    assert w.db.execute("SELECT COUNT(*) FROM capas").fetchone()[0] == 1
    assert patrol.finding_to_capa(w.db, w.owner, a2, w.capa_form())[0] is None
    assert patrol.finding_to_capa(w.db, w.insp_b, a2, w.capa_form()) == (None, patrol.NOT_FOUND)
    assert patrol.finding_to_capa(w.db, w.plain, a2, w.capa_form(owner_id="9999"))[0] is None


def test_finding_to_capa_default_title_is_the_item_text(w):
    _, (a1, a2) = w.submitted_round()
    cid, errors = patrol.finding_to_capa(w.db, w.insp, a2, w.capa_form(title=""))
    assert errors == [] and w.db.execute("SELECT title FROM capas WHERE id=?", (cid,)).fetchone()[0] == "ข้อ 2"


# ---------- ประตูปิดรายการ ----------
def hazard_ready(w, level_requires=0):
    hid = add_hazard(w.db, w.a["a"], w.owner["id"], status="assigned", owner_id=w.insp["id"], due_date="2026-04-01", level_requires_approval=level_requires)
    return hid


def hazard_status(w, hid):
    return w.db.execute("SELECT status FROM hazards WHERE id=?", (hid,)).fetchone()[0]


def test_gate_off_by_default_closing_unchanged(w):
    hid = hazard_ready(w)
    capa.create(w.db, w.insp, w.capa_form(), ("hazard", hid))
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") == [] and hazard_status(w, hid) == "closed"


def turn_gate(w, on=True):
    w.db.execute("UPDATE app_settings SET value=? WHERE key='require_capa_verified'", ("1" if on else "0",)); w.db.commit()


def test_gate_on_blocks_hazard_close_until_all_capas_verified_and_ignores_cancelled(w):
    turn_gate(w)
    hid = hazard_ready(w)
    c1, _ = capa.create(w.db, w.insp, w.capa_form(), ("hazard", hid))
    c2, _ = capa.create(w.db, w.insp, w.capa_form(title="ที่สอง"), ("hazard", hid))
    errors = workflow.close(w.db, w.verifier, hid, "แก้แล้ว")
    assert len(errors) == 1 and "2" in errors[0] and hazard_status(w, hid) == "assigned"
    assert w.db.execute("SELECT COUNT(*) FROM hazard_events WHERE kind='closed'").fetchone()[0] == 0
    capa.cancel(w.db, w.insp, c2, "ซ้ำซ้อน")
    capa.complete(w.db, w.owner, c1, "ทำแล้ว")
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") and "1" in workflow.close(w.db, w.verifier, hid, "แก้แล้ว")[0]
    assert capa.verify(w.db, w.verifier, c1, "ตรวจแล้ว") == []
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") == [] and hazard_status(w, hid) == "closed"
    other = hazard_ready(w)                                                                              # ไม่มี CAPA → ปิดได้
    assert workflow.close(w.db, w.verifier, other, "ok") == []


def test_gate_on_blocks_incident_close_message_names_remaining_count(w):
    turn_gate(w)
    iid, errs = incidents.report(w.db, w.insp, {"kind": "near_miss", "occurred_at": "2026-03-09T08:00", "risk_area_id": str(w.a["a"]), "title": "เฉียดชน",
                                                 "likelihood": "2", "severity": "2"})
    assert errs == []
    incident_flow.assign_investigator(w.db, w.admin, iid, w.insp["id"], "")
    cause = w.db.execute("SELECT id FROM incident_causes LIMIT 1").fetchone()[0]
    incident_flow.record_investigation(w.db, w.insp, iid, cause, "ราก", "มาตรการ", "")
    cid, _ = capa.create(w.db, w.insp, w.capa_form(), ("incident", iid))
    err = incident_flow.close(w.db, w.admin2, iid, "เสร็จ")
    assert len(err) == 1 and "1" in err[0] and w.db.execute("SELECT status FROM incidents WHERE id=?", (iid,)).fetchone()[0] == "actions_set"
    capa.complete(w.db, w.owner, cid, "ทำแล้ว"); capa.verify(w.db, w.verifier, cid, "ok")
    assert incident_flow.close(w.db, w.admin2, iid, "เสร็จ") == []


def test_gate_checked_inside_the_close_transaction_so_a_capa_created_in_between_is_seen(w, monkeypatch):
    turn_gate(w)
    hid = hazard_ready(w)
    real = capa.gate_errors
    calls = []
    def late_capa(db, kind, sid):
        calls.append((kind, sid))
        if len(calls) == 1:                                                                               # ระหว่างเริ่มปิดกับตรวจประตู มี CAPA ใหม่โผล่
            db.execute("INSERT INTO capas(code, source_kind, source_id, action_type, title, risk_area_id, owner_id, due_date, created_by) "
                       "VALUES('CP-RACE','hazard',?, 'corrective','ใหม่',?,?, '2026-04-01',?)", (sid, w.a["a"], w.owner["id"], w.insp["id"]))
        return real(db, kind, sid)
    monkeypatch.setattr(capa, "gate_errors", late_capa)
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") and hazard_status(w, hid) == "assigned"
    assert w.db.execute("SELECT COUNT(*) FROM hazard_events WHERE kind='closed'").fetchone()[0] == 0


def test_closed_hazard_with_gate_on_still_notifies_once(w):
    turn_gate(w)
    hid = hazard_ready(w)
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") == []
    assert w.db.execute("SELECT COUNT(*) FROM hazard_events WHERE kind='closed'").fetchone()[0] == 1
    assert w.db.execute("SELECT COUNT(*) FROM notifications WHERE event='hazard_closed'").fetchone()[0] <= 1


def test_gate_counts_capas_of_the_patrol_finding_a_hazard_was_created_from(w):
    turn_gate(w)
    _, (a1, a2) = w.submitted_round()
    hid, _ = patrol.finding_to_hazard(w.db, w.insp, a2, w.type_id, w.source_id, w.root)
    cid, errs = patrol.finding_to_capa(w.db, w.insp, a2, w.capa_form())
    assert errs == [] and capa.open_blockers(w.db, "hazard", hid) == 1
    w.db.execute("UPDATE hazards SET status='assigned', owner_id=?, due_date='2026-04-01' WHERE id=?", (w.insp["id"], hid)); w.db.commit()
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") and hazard_status(w, hid) == "assigned"
    capa.complete(w.db, w.owner, cid, "ทำแล้ว"); capa.verify(w.db, w.verifier, cid, "ok")
    assert workflow.close(w.db, w.verifier, hid, "แก้แล้ว") == []
    other = patrol.finding_to_capa(w.db, w.insp, a1, w.capa_form())[0]                                    # CAPA ของข้ออื่นไม่เกี่ยว
    assert other and capa.open_blockers(w.db, "hazard", hid) == 0
