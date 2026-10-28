from datetime import date

import pytest

from safety import patrol
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_user


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.ch = add_chain(db, "P")
        self.insp = add_user(db, "assessor", "ผู้ตรวจ")
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','แบบตรวจ 1')").lastrowid
        db.execute("INSERT INTO checklist_items(checklist_id, section, text_th) VALUES(?,?,?)", (self.cl, "หมวด", "ข้อ 1"))
        db.commit()

    def plan(self, freq="weekly", created="2026-01-01 08:00:00", **kw):
        cols = {"checklist_id": self.cl, "risk_area_id": self.ch["a"], "frequency": freq, "created_at": created, **kw}
        cur = self.db.execute(f"INSERT INTO patrol_plans({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
        self.db.commit()
        return self.db.execute("SELECT * FROM patrol_plans WHERE id=?", (cur.lastrowid,)).fetchone()

    def submitted(self, when: str, n=[0]):
        n[0] += 1
        self.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status, submitted_at) VALUES(?,?,?,?, 'submitted', ?)",
                        (f"PT-X-{n[0]:04d}-{when}", self.cl, self.ch["a"], self.insp, when))
        self.db.commit()


@pytest.fixture
def w(db):
    return W(db)


D = date


@pytest.mark.parametrize("freq, last, today, due, state, days", [
    ("daily", None, D(2026, 1, 1), D(2026, 1, 1), "due", 0),                              # ไม่เคยตรวจ: ครบกำหนดตั้งแต่วันสร้างแผน
    ("daily", None, D(2026, 1, 5), D(2026, 1, 1), "overdue", 4),
    ("daily", "2026-03-10 09:00:00", D(2026, 3, 10), D(2026, 3, 11), "ok", 1),
    ("daily", "2026-03-10 09:00:00", D(2026, 3, 11), D(2026, 3, 11), "due", 0),
    ("daily", "2026-03-06 09:00:00", D(2026, 3, 9), D(2026, 3, 7), "overdue", 2),         # วันศุกร์ → ทวงวันจันทร์: เลยมา 2 วัน (ไม่ข้ามเสาร์-อาทิตย์)
    ("weekly", "2026-03-03 09:00:00", D(2026, 3, 10), D(2026, 3, 10), "due", 0),
    ("weekly", "2026-03-03 09:00:00", D(2026, 3, 9), D(2026, 3, 10), "ok", 1),
    ("weekly", "2026-03-03 09:00:00", D(2026, 3, 11), D(2026, 3, 10), "overdue", 1),
    ("weekly", "2026-01-05 09:00:00", D(2026, 3, 30), D(2026, 1, 12), "overdue", 77),    # พลาดหลายรอบ = สถานะเดียว
    ("monthly", "2026-01-31 10:00:00", D(2026, 2, 28), D(2026, 2, 28), "due", 0),         # 31 ม.ค. → 28 ก.พ.
    ("monthly", "2028-01-31 10:00:00", D(2028, 2, 28), D(2028, 2, 29), "ok", 1),          # ปีอธิกสุรทิน → 29 ก.พ.
    ("monthly", "2025-12-15 10:00:00", D(2026, 1, 14), D(2026, 1, 15), "ok", 1),          # ข้ามปี
    ("monthly", "2026-03-30 10:00:00", D(2026, 4, 30), D(2026, 4, 30), "due", 0),
])
def test_plan_status_table(w, freq, last, today, due, state, days):
    plan = w.plan(freq)
    if last:
        w.submitted(last)
    st = patrol.plan_status(w.db, plan, today)
    assert (st["due"], st["state"], st["days"]) == (due, state, days)
    assert st["last"] == (date.fromisoformat(last[:10]) if last else None)


def test_status_uses_latest_submitted_round_for_that_checklist_and_area_only_and_ignores_drafts(w):
    plan = w.plan("weekly")
    w.submitted("2026-03-01 09:00:00"); w.submitted("2026-03-08 09:00:00")
    other = add_chain(w.db, "Q")
    w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status, submitted_at) VALUES('PT-OTHER',?,?,?, 'submitted','2026-03-09 09:00:00')",
                 (w.cl, other["a"], w.insp))
    w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status) VALUES('PT-DRAFT',?,?,?, 'draft')", (w.cl, w.ch["a"], w.insp))
    w.db.commit()
    assert patrol.plan_status(w.db, plan, D(2026, 3, 9))["due"] == D(2026, 3, 15)


# ================= รอบตรวจ =================
import io
import threading
from datetime import datetime

from PIL import Image

from safety import timeutil
from safety.schema import ensure_schema as _ensure
from tests.safety.factories import user_row


def png():
    buf = io.BytesIO(); Image.new("RGB", (30, 30), (9, 9, 9)).save(buf, "PNG"); return buf.getvalue()


class R:
    """ฉาก: แบบตรวจ 3 ข้อ (น้ำหนัก 1/2/3, ข้อ 2 ต้องแนบรูปเมื่อไม่ผ่าน), ผู้ตรวจ/หัวหน้า/ผู้แจ้ง/แอดมิน และอีกโรงงาน"""
    def __init__(self, db, tmp_path):
        self.db, self.root = db, str(tmp_path / "up")
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        u = lambda role, name, **kw: user_row(db, add_user(db, role, name, **kw))
        db.execute("INSERT INTO safety_roles(key,name_th,permissions) VALUES('inspector_only','ผู้ตรวจ (ดูเฉพาะของตน)','patrol.perform')")
        self.insp, self.insp2 = u("assessor", "ผู้ตรวจ"), u("inspector_only", "ผู้ตรวจ 2")
        self.insp_b = u("assessor", "ผู้ตรวจ B", factory_id=self.b["f"])
        self.head, self.rep, self.admin = u("dept_head", "หัวหน้า"), u("reporter", "ผู้แจ้ง"), u("admin", "แอดมิน")
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','ตรวจ')").lastrowid
        self.items = [db.execute("INSERT INTO checklist_items(checklist_id, section, text_th, weight, require_photo_on_fail, sort) VALUES(?,?,?,?,?,?)",
                                 (self.cl, "หมวด", f"ข้อ {n}", n, 1 if n == 2 else 0, n)).lastrowid for n in (1, 2, 3)]
        db.commit()

    def start(self, user=None, **kw):
        rid, errors = patrol.start_round(self.db, user or self.insp, **{"checklist_id": self.cl, "risk_area_id": self.a["a"], **kw})
        assert errors == [], errors
        return rid

    def answers(self, rid):
        return self.db.execute("SELECT * FROM patrol_answers WHERE round_id=? ORDER BY sort, id", (rid,)).fetchall()

    def fill(self, rid, results, user=None, **extra):
        form = {}
        for a, res in zip(self.answers(rid), results):
            form[f"result_{a['id']}"] = res
        return patrol.save_answers(self.db, user or self.insp, rid, {**form, **extra})

    def photo(self, rid, index=1, user=None):
        aid = self.answers(rid)[index]["id"]
        assert patrol.add_photo(self.db, user or self.insp, rid, aid, "a.png", png(), self.root) == []
        return aid


@pytest.fixture
def r(db, tmp_path):
    return R(db, tmp_path)


def test_start_snapshots_active_items_in_order_and_reuses_open_draft(r):
    r.db.execute("UPDATE checklist_items SET active=0 WHERE id=?", (r.items[2],)); r.db.commit()
    rid = r.start()
    rows = r.answers(rid)
    assert [(a["text_th"], a["weight"], a["require_photo"], a["section"]) for a in rows] == [("ข้อ 1", 1, 0, "หมวด"), ("ข้อ 2", 2, 1, "หมวด")]
    round_ = r.db.execute("SELECT * FROM patrol_rounds WHERE id=?", (rid,)).fetchone()
    assert round_["code"].startswith("PT-") and round_["status"] == "draft" and round_["inspector_id"] == r.insp["id"] and round_["version"] == 0
    assert r.start() == rid                                                                           # เริ่มซ้ำ → ร่างเดิม
    assert r.start(r.insp2) != rid                                                                    # คนละผู้ตรวจ → ร่างของตัวเอง
    assert r.db.execute("SELECT COUNT(*) FROM patrol_rounds").fetchone()[0] == 2


def test_start_links_the_matching_active_plan(r):
    pid = r.db.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency) VALUES(?,?, 'daily')", (r.cl, r.a["a"])).lastrowid; r.db.commit()
    assert r.db.execute("SELECT plan_id FROM patrol_rounds WHERE id=?", (r.start(),)).fetchone()[0] == pid
    rid2, _ = patrol.start_round(r.db, r.insp2, plan_id=pid)
    assert r.db.execute("SELECT checklist_id, risk_area_id, plan_id FROM patrol_rounds WHERE id=?", (rid2,)).fetchone()[:] == (r.cl, r.a["a"], pid)


def test_start_refuses_out_of_scope_inactive_empty_or_unprivileged(r):
    for kw in ({"risk_area_id": 9999}, {"checklist_id": 9999}, {"risk_area_id": None}, {"checklist_id": None}):
        rid, errors = patrol.start_round(r.db, r.insp, **{"checklist_id": r.cl, "risk_area_id": r.a["a"], **kw})
        assert rid is None and errors, kw
    scoped, errors = patrol.start_round(r.db, r.insp_b, checklist_id=r.cl, risk_area_id=r.a["a"])
    assert scoped is None and errors                                                                  # พื้นที่ของโรงงาน A
    for user in (r.head, r.rep):
        assert patrol.start_round(r.db, user, checklist_id=r.cl, risk_area_id=r.a["a"]) == (None, ["คุณไม่มีสิทธิ์ตรวจความปลอดภัย"])
    r.db.execute("UPDATE checklists SET active=0"); r.db.commit()
    assert patrol.start_round(r.db, r.insp, checklist_id=r.cl, risk_area_id=r.a["a"])[0] is None
    empty = r.db.execute("INSERT INTO checklists(code,name_th) VALUES('EMPTY','ว่าง')").lastrowid; r.db.commit()
    assert patrol.start_round(r.db, r.insp, checklist_id=empty, risk_area_id=r.a["a"])[0] is None
    assert patrol.start_round(r.db, r.insp, plan_id=9999)[0] is None
    assert r.db.execute("SELECT COUNT(*) FROM patrol_rounds").fetchone()[0] == 0


def test_save_answers_validates_ids_results_and_lengths_and_bumps_version(r):
    rid = r.start()
    a = r.answers(rid)
    assert patrol.save_answers(r.db, r.insp, rid, {f"result_{a[0]['id']}": "pass", f"note_{a[0]['id']}": "ปกติ", f"result_{a[1]['id']}": "fail"}) == []
    assert [(x["result"], x["note"]) for x in r.answers(rid)] == [("pass", "ปกติ"), ("fail", ""), (None, "")]
    assert r.db.execute("SELECT version FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0] == 1
    other = r.start(r.insp2)
    foreign = r.answers(other)[0]["id"]
    for form in ({f"result_{a[0]['id']}": "maybe"}, {f"note_{a[0]['id']}": "ก" * 501}, {f"result_{foreign}": "pass"}, {"result_999999": "pass"}):
        assert patrol.save_answers(r.db, r.insp, rid, form), form
    assert [x["result"] for x in r.answers(other)] == [None, None, None]
    assert patrol.save_answers(r.db, r.insp, rid, {f"result_{a[0]['id']}": ""}) == [] and r.answers(rid)[0]["result"] is None       # ว่าง = ล้างคำตอบ
    assert patrol.save_answers(r.db, r.insp, rid, {"result_1": "pass"}, expected_version=0) == patrol.STALE_FORM


def test_only_inspector_or_admin_edits_draft_others_get_not_found(r):
    rid = r.start()
    aid = r.answers(rid)[0]["id"]
    for user in (r.insp2, r.insp_b, r.head, r.rep):
        assert patrol.save_answers(r.db, user, rid, {f"result_{aid}": "pass"}) == patrol.NOT_FOUND
        assert patrol.submit_round(r.db, user, rid) == patrol.NOT_FOUND
        assert patrol.add_photo(r.db, user, rid, aid, "a.png", png(), r.root) == patrol.NOT_FOUND
    assert patrol.save_answers(r.db, r.admin, rid, {f"result_{aid}": "pass"}) == []


def test_submit_requires_all_items_answered(r):
    rid = r.start()
    r.fill(rid, ["pass", "pass", ""])
    errors = patrol.submit_round(r.db, r.insp, rid)
    assert errors and "ตอบ" in errors[0] and r.db.execute("SELECT status FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0] == "draft"


def test_submit_requires_photo_on_required_fail_and_photo_must_belong_to_that_answer(r):
    rid = r.start()
    r.fill(rid, ["pass", "fail", "pass"])
    assert patrol.submit_round(r.db, r.insp, rid)                                                     # ข้อ 2 ต้องมีรูป
    other = r.start(r.insp2)
    r.photo(other, 1, r.insp2)                                                                        # รูปของข้อเดียวกันในรอบอื่น ไม่ช่วยรอบนี้
    r.photo(rid, 0)                                                                                   # รูปของข้ออื่นในรอบนี้ ไม่ช่วยข้อ 2
    assert patrol.submit_round(r.db, r.insp, rid)
    r.photo(rid, 1)
    assert patrol.submit_round(r.db, r.insp, rid) == []


def test_fail_without_required_photo_flag_needs_no_photo(r):
    rid = r.start()
    r.fill(rid, ["fail", "pass", "pass"])
    assert patrol.submit_round(r.db, r.insp, rid) == []


def test_counts_and_weighted_score_na_excluded_and_none_when_nothing_scored(r):
    rid = r.start()
    r.fill(rid, ["pass", "pass", "fail"])                                                              # น้ำหนัก 1+2 ผ่าน, 3 ไม่ผ่าน → 3/6 = 50
    assert patrol.submit_round(r.db, r.insp, rid) == []
    row = r.db.execute("SELECT * FROM patrol_rounds WHERE id=?", (rid,)).fetchone()
    assert (row["status"], row["passed"], row["failed"], row["na"], row["score_pct"], row["version"]) == ("submitted", 2, 1, 0, 50, 2)
    assert row["submitted_at"] and row["submitted_at"][:4].isdigit()
    r2 = r.start(r.insp2)
    r.fill(r2, ["na", "pass", "pass"], r.insp2)                                                        # (2+3)/(2+3) = 100, N/A ไม่นับ
    patrol.submit_round(r.db, r.insp2, r2)
    assert r.db.execute("SELECT score_pct, na FROM patrol_rounds WHERE id=?", (r2,)).fetchone()[:] == (100, 1)
    r3 = r.start(r.admin)
    r.fill(r3, ["na", "na", "na"], r.admin)
    patrol.submit_round(r.db, r.admin, r3)
    assert r.db.execute("SELECT score_pct FROM patrol_rounds WHERE id=?", (r3,)).fetchone()[0] is None


def test_rounding_of_score(r):
    rid = r.start()
    r.fill(rid, ["pass", "fail", "pass"]); r.photo(rid, 1)                                              # (1+3)/6 = 66.67 → 67
    patrol.submit_round(r.db, r.insp, rid)
    assert r.db.execute("SELECT score_pct FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0] == 67


def test_submitted_round_rejects_save_photo_resubmit_delete_and_direct_sql(r):
    rid = r.start()
    r.fill(rid, ["pass", "pass", "pass"])
    assert patrol.submit_round(r.db, r.insp, rid) == []
    aid = r.answers(rid)[0]["id"]
    assert patrol.save_answers(r.db, r.insp, rid, {f"result_{aid}": "fail"}) and patrol.submit_round(r.db, r.insp, rid)
    assert patrol.add_photo(r.db, r.insp, rid, aid, "a.png", png(), r.root) and patrol.delete_draft(r.db, r.insp, rid, r.root)
    assert patrol.save_answers(r.db, r.admin, rid, {f"result_{aid}": "fail"})
    import sqlite3
    for stmt in ("UPDATE patrol_answers SET result='fail'", "DELETE FROM patrol_answers", "UPDATE patrol_rounds SET failed=9", "DELETE FROM patrol_rounds"):
        with pytest.raises(sqlite3.IntegrityError):
            r.db.execute(stmt)
    assert r.db.execute("SELECT failed FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0] == 0


def test_submit_twice_race_two_connections_exactly_one_wins(tmp_path):
    from safety.db import connect as conn
    path = str(tmp_path / "race.db")
    con = conn(path); _ensure(con)
    world = R(con, tmp_path)
    rid = world.start()
    world.fill(rid, ["pass", "pass", "pass"])
    results = {}
    barrier = threading.Barrier(2)

    def run(name):
        c = conn(path)
        try:
            user = user_row(c, world.insp["id"])
            barrier.wait()
            results[name] = patrol.submit_round(c, user, rid)
        finally:
            c.close()
    ts = [threading.Thread(target=run, args=(n,)) for n in ("a", "b")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(len(v) for v in results.values()) == [0, 1], results
    row = con.execute("SELECT status, passed, version FROM patrol_rounds WHERE id=?", (rid,)).fetchone()
    assert (row["status"], row["passed"], row["version"]) == ("submitted", 3, 2)


def test_delete_draft_by_inspector_or_admin_only_and_removes_files(r):
    rid = r.start()
    aid = r.photo(rid)
    folder = r.db.execute("SELECT stored_name FROM patrol_attachments WHERE answer_id=?", (aid,)).fetchone()[0]
    import os
    assert os.path.isfile(os.path.join(r.root, f"patrol-{aid}", folder))
    assert patrol.delete_draft(r.db, r.insp2, rid, r.root) == patrol.NOT_FOUND
    assert patrol.delete_draft(r.db, r.insp, rid, r.root) == []
    assert not os.path.exists(os.path.join(r.root, f"patrol-{aid}", folder))
    assert r.db.execute("SELECT COUNT(*) FROM patrol_rounds").fetchone()[0] == r.db.execute("SELECT COUNT(*) FROM patrol_answers").fetchone()[0] == 0


def test_visibility_scope_and_view_all(r):
    rid = r.start()
    r.fill(rid, ["pass", "pass", "pass"]); patrol.submit_round(r.db, r.insp, rid)
    vis = lambda u: patrol.visible_round(r.db, u, rid) is not None
    assert vis(r.insp) and vis(r.admin) and vis(r.head)                                                # หัวหน้ามี patrol.view
    assert not vis(r.insp2) and not vis(r.insp_b) and not vis(r.rep)                                   # ผู้ตรวจคนอื่นเห็นเฉพาะของตัวเอง; อีกโรงงาน/ผู้แจ้งไม่เห็น
    assert patrol.visible_round(r.db, r.insp, 9999) is None
    assert [x["id"] for x in patrol.rounds_for(r.db, r.insp2)] == [] and [x["id"] for x in patrol.rounds_for(r.db, r.head)] == [rid]


def test_latest_rounds_only_submitted_in_scope_newest_first_and_limit(r):
    r.db.execute("UPDATE checklist_items SET require_photo_on_fail=0"); r.db.commit()
    ids = []
    for i in range(7):
        rid = r.start(r.admin)
        r.fill(rid, ["pass", "fail" if i % 2 else "pass", "pass"], r.admin)
        patrol.submit_round(r.db, r.admin, rid)
        ids.append(rid)
    r.start(r.admin)                                                                                    # ร่างไม่ปรากฏ
    got = patrol.latest_rounds(r.db, r.admin, limit=5)
    assert [g["id"] for g in got] == ids[::-1][:5]
    assert {"code", "area_name", "inspector_name", "submitted_at", "passed", "failed", "na", "score_pct"} <= set(got[0].keys())
    assert patrol.latest_rounds(r.db, r.insp_b) == [] and len(patrol.latest_rounds(r.db, r.admin, limit=2)) == 2


def test_checklist_edit_during_draft_does_not_change_snapshot(r):
    rid = r.start()
    r.db.execute("UPDATE checklist_items SET text_th='เปลี่ยนแล้ว', weight=9, active=0 WHERE id=?", (r.items[0],)); r.db.commit()
    assert [a["text_th"] for a in r.answers(rid)][0] == "ข้อ 1" and [a["weight"] for a in r.answers(rid)][0] == 1
    r.fill(rid, ["pass", "pass", "pass"])
    assert patrol.submit_round(r.db, r.insp, rid) == [] and r.db.execute("SELECT passed FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0] == 3


def test_due_plans_lists_visible_due_and_overdue_most_overdue_first(r):
    c2 = r.db.execute("INSERT INTO checklists(code,name_th) VALUES('CL2','สอง')").lastrowid
    r.db.execute("INSERT INTO checklist_items(checklist_id,text_th) VALUES(?, 'x')", (c2,))
    p1 = r.db.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency, created_at) VALUES(?,?, 'daily', '2026-03-01 08:00:00')", (r.cl, r.a["a"])).lastrowid
    p2 = r.db.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency, created_at) VALUES(?,?, 'daily', '2026-03-05 08:00:00')", (c2, r.a["a"])).lastrowid
    p3 = r.db.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency, created_at) VALUES(?,?, 'daily', '2026-03-20 08:00:00')", (c2, r.b["a"])).lastrowid
    r.db.commit()
    got = patrol.due_plans(r.db, r.insp, date(2026, 3, 10))
    assert [(g["id"], g["status"]["state"]) for g in got] == [(p1, "overdue"), (p2, "overdue")]        # p3 ยังไม่ครบ/คนละโรงงาน
    assert [g["id"] for g in patrol.due_plans(r.db, r.admin, date(2026, 3, 25))] == [p1, p2, p3]
    assert patrol.due_plans(r.db, r.rep, date(2026, 3, 25)) == []
