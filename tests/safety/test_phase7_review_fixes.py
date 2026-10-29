import sqlite3

import pytest

from safety import capa, notify, patrol, patrol_admin as pa
from safety.db import connect
from safety.schema import ensure_schema
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_user, user_row
from tests.safety.helpers import login
from tests.safety.test_capa import TODAY, W as CW, fixed  # noqa: F401  (fixed = autouse clock)


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


@pytest.fixture
def w(db):
    return CW(db)


def test_verifier_who_ever_marked_done_cannot_verify_after_reopen_loop(w):
    cid = w.new()
    assert capa.complete(w.db, w.verifier, cid, "ผู้ยืนยันกดเสร็จแทน") == []
    assert capa.reopen(w.db, w.admin, cid, "ตีกลับ") == []
    assert capa.complete(w.db, w.owner, cid, "เจ้าของทำเสร็จอีกครั้ง") == []
    assert "ไม่ใช่ผู้ที่กดว่าเสร็จ" in capa.verify(w.db, w.verifier, cid, "ยืนยันเอง")[0]
    assert capa.verify(w.db, w.admin2, cid, "คนอื่น") == []


def test_capa_reassign_with_new_due_date_gets_fresh_reminders(w):
    cid = w.new()
    assert notify.emit_capa(w.db, "capa_due_soon", cid, None, offset_days=2, days=2) > 0
    first = len(w.notes("capa_due_soon"))
    w.db.execute("UPDATE capas SET due_date=? WHERE id=?", ("2026-06-30", cid))
    w.db.commit()
    notify.emit_capa(w.db, "capa_due_soon", cid, None, offset_days=2, days=2)
    assert len(w.notes("capa_due_soon")) > first


class P:
    def __init__(self, db):
        self.db = db
        self.ch = add_chain(db, "P")
        self.u = add_user(db, "assessor", "ผู้ตรวจ")
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','แบบตรวจ')").lastrowid
        self.item = db.execute("INSERT INTO checklist_items(checklist_id, section, text_th) VALUES(?,?,?)", (self.cl, "ห", "ข้อ")).lastrowid
        db.commit()

    def round(self, status, code):
        rid = self.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status) VALUES(?,?,?,?,?)",
                              (code, self.cl, self.ch["a"], self.u, "draft")).lastrowid
        aid = self.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th) VALUES(?,?,?,?)", (rid, self.item, "ห", "ข้อ")).lastrowid
        self.db.execute("UPDATE patrol_rounds SET status=? WHERE id=?", (status, rid))
        self.db.commit()
        return rid, aid


def test_triggers_block_moving_rows_into_a_submitted_round(db):
    p = P(db)
    sub_r, sub_a = p.round("submitted", "PT-1")
    _, draft_a = p.round("draft", "PT-2")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE patrol_answers SET round_id=? WHERE id=?", (sub_r, draft_a))
    db.rollback()
    att = db.execute("INSERT INTO patrol_attachments(answer_id, stored_name, orig_name, size) VALUES(?,?,?,?)", (draft_a, "x.jpg", "x.jpg", 1)).lastrowid
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE patrol_attachments SET answer_id=? WHERE id=?", (sub_a, att))
    db.rollback()


def test_plan_admin_is_scoped_to_users_factory(db):
    a, b = add_chain(db, "A"), add_chain(db, "B")
    cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('C','ก')").lastrowid
    pid = db.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency) VALUES(?,?,?)", (cl, b["a"], "weekly")).lastrowid
    db.commit()
    mgr_a = user_row(db, add_user(db, "admin", "ผู้จัดการ A", factory_id=a["f"]))
    assert pa.update_plan(db, mgr_a, pid, {"frequency": "daily"}) == ["ไม่พบแผนการตรวจ"]
    assert pa.toggle_plan(db, mgr_a, pid) == ["ไม่พบแผนการตรวจ"]
    assert db.execute("SELECT frequency, active FROM patrol_plans WHERE id=?", (pid,)).fetchone()[:] == ("weekly", 1)
    assert pa.plans_for(db, mgr_a) == []


def test_photo_racing_a_submit_returns_message_not_error(db, tmp_path, monkeypatch):
    p = P(db)
    rid, aid = p.round("draft", "PT-3")
    inspector = user_row(db, p.u)
    from safety import images
    def boom(*a, **k):
        raise sqlite3.IntegrityError("submitted patrol round is immutable")
    monkeypatch.setattr(patrol, "save_attachment", boom)
    assert patrol.add_photo(db, inspector, rid, aid, "a.png", b"x", str(tmp_path)) == ["รอบตรวจนี้ส่งแล้ว เพิ่มรูปไม่ได้"]


def test_plain_viewer_who_owns_a_capa_can_open_and_start_it(safety_app):
    c = login(safety_app, role="viewer", name="เจ้าของ")
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "V")
    cid = con.execute("INSERT INTO capas(code, source_kind, action_type, title, risk_area_id, owner_id, due_date, created_by) VALUES('CP-1','manual','corrective','t',?,?,?,?)",
                      (ch["a"], c.uid, "2026-12-01", c.uid)).lastrowid
    con.commit()
    con.close()
    assert c.get(f"/capas/{cid}").status_code == 200
    r = c.post(f"/capas/{cid}/start", data={"_csrf": csrf(c)})
    assert r.status_code == 302
    con = connect(safety_app.config["DATABASE"])
    assert con.execute("SELECT status FROM capas WHERE id=?", (cid,)).fetchone()[0] == "in_progress"
