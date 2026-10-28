import json
from datetime import date, datetime

import pytest

from safety import incidents, timeutil
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_incident, add_user, severity_id, user_row

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
        self.rep = user_row(db, add_user(db, "reporter", "ผู้แจ้ง"))
        self.rep2 = user_row(db, add_user(db, "reporter", "ผู้แจ้ง 2"))
        self.inv = user_row(db, add_user(db, "assessor", "ผู้สอบสวน"))
        self.inv_b = user_row(db, add_user(db, "assessor", "ผู้สอบสวน B", factory_id=self.b["f"]))
        self.viewer = user_row(db, add_user(db, "viewer", "ผู้ดู"))
        self.admin = user_row(db, add_user(db, "admin", "แอดมิน"))

    def form(self, **kw):
        base = {"kind": "accident", "occurred_at": "2026-03-10T08:15", "risk_area_id": str(self.a["a"]), "title": "รถตักชนเสา",
                "description": "รายละเอียด", "immediate_action": "ปฐมพยาบาล", "injury_severity_id": str(severity_id(self.db, "first_aid"))}
        base.update({k: v for k, v in kw.items()})
        return base

    def report(self, user=None, **kw):
        return incidents.report(self.db, user or self.rep, self.form(**kw))


@pytest.fixture
def w(db):
    return W(db)


def test_report_accident_creates_row_event_and_notification(w):
    form = w.form(people_name_0="สมชาย ใจดี", people_no_0="E1", people_role_0="injured", people_note_0="แผลถลอก")
    iid, errors = incidents.report(w.db, w.rep, form)
    assert errors == [] and iid
    row = w.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    assert row["code"] == "IN-2603-0001" and row["status"] == "reported" and row["kind"] == "accident" and row["version"] == 0
    assert row["occurred_at"] == "2026-03-10 08:15" and row["injury_severity_id"] == severity_id(w.db, "first_aid")
    ev = w.db.execute("SELECT * FROM incident_events WHERE incident_id=?", (iid,)).fetchall()
    assert [(e["kind"], e["from_status"], e["to_status"]) for e in ev] == [("reported", None, "reported")]
    assert json.loads(ev[0]["data"]) == {"kind": "accident", "people": 1}
    assert w.db.execute("SELECT COUNT(*) FROM incident_people WHERE incident_id=?", (iid,)).fetchone()[0] == 1
    assert w.db.execute("SELECT COUNT(*) FROM notifications WHERE incident_id=?", (iid,)).fetchone()[0] >= 1


def test_report_near_miss_snapshots_level_like_hazards(w):
    iid, errors = w.report(kind="near_miss", likelihood="5", severity="5", injury_severity_id=str(severity_id(w.db, "fatality")))
    assert errors == []
    row = w.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    assert (row["score"], row["level_name"], row["level_requires_approval"]) == (25, "สูงมาก", 1) and row["level_color"]
    assert row["injury_severity_id"] is None                                                       # Near Miss ไม่มีระดับการบาดเจ็บ


def test_near_miss_needs_valid_potential_scores(w):
    for kw in ({}, {"likelihood": "6", "severity": "1"}, {"likelihood": "x", "severity": "1"}, {"likelihood": "1"}):
        iid, errors = w.report(kind="near_miss", **kw)
        assert iid is None and errors, kw
    assert w.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_accident_requires_active_severity(w):
    assert w.report(injury_severity_id="")[0] is None
    assert w.report(injury_severity_id="9999")[0] is None
    w.db.execute("UPDATE injury_severities SET active=0 WHERE code='first_aid'"); w.db.commit()
    assert w.report()[0] is None and w.report(injury_severity_id=str(severity_id(w.db, "medical")))[0]


@pytest.mark.parametrize("raw, ok", [
    ("2026-03-10T14:30", True), ("2026-03-10 14:30", True), ("2026-03-10T14:31", False),          # อนาคตแม้แต่นาทีเดียว
    ("2026-03-11T00:00", False), ("2021-03-10T14:30", True), ("2021-03-09T23:59", False),            # เกิน 5 ปี
    ("2024-02-29T10:00", True), ("2026-02-29T10:00", False), ("2026-13-01T10:00", False),
    ("", False), ("พรุ่งนี้", False), ("2026-03-10", False), ("2026-03-10T25:00", False),
])
def test_occurred_at_boundaries(w, raw, ok):
    iid, errors = w.report(occurred_at=raw)
    assert bool(iid) is ok and bool(errors) is not ok
    if not ok:
        assert w.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0 and w.db.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0] == 0


def test_validation_limits_insert_nothing(w):
    cases = [dict(title="ก" * 201), dict(title="  "), dict(description="ก" * 2001), dict(immediate_action="ก" * 1001),
             dict(people_name_0="ก" * 121), dict(people_name_0="x", people_no_0="1" * 31), dict(people_name_0="x", people_note_0="ก" * 301),
             dict(people_name_0="x", people_role_0="alien"), dict(kind="other")]
    cases.append({f"people_name_{i}": f"คน{i}" for i in range(21)})                                   # 21 คน
    for kw in cases:
        iid, errors = w.report(**kw)
        assert iid is None and errors, kw
    assert w.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert w.report(**{f"people_name_{i}": f"คน{i}" for i in range(20)})[0]                           # 20 คนพอดีผ่าน


def test_blank_people_rows_skipped_and_role_defaults_to_injured(w):
    iid, _ = w.report(people_name_0="  ", people_name_1="สมหญิง", people_role_1="")
    rows = w.db.execute("SELECT * FROM incident_people WHERE incident_id=?", (iid,)).fetchall()
    assert [(r["name"], r["role"]) for r in rows] == [("สมหญิง", "injured")]


def test_area_must_be_in_scope_and_active(w):
    scoped = user_row(w.db, add_user(w.db, "reporter", "ในโรงงาน B", factory_id=w.b["f"]))
    assert incidents.report(w.db, scoped, w.form())[0] is None                                         # พื้นที่ของโรงงาน A
    assert incidents.report(w.db, scoped, w.form(risk_area_id=str(w.b["a"])))[0]
    w.db.execute("UPDATE risk_areas SET active=0 WHERE id=?", (w.a["a"],)); w.db.commit()
    assert w.report()[0] is None
    assert w.report(risk_area_id="abc")[0] is None


def test_report_requires_permission(w):
    assert incidents.report(w.db, w.viewer, w.form()) == (None, ["คุณไม่มีสิทธิ์แจ้งอุบัติเหตุ"])


def test_related_hazard_must_be_visible(w):
    mine = add_hazard(w.db, w.a["a"], w.rep["id"])
    theirs = add_hazard(w.db, w.a["a"], w.rep2["id"])
    assert w.report(related_hazard_id=str(mine))[0]
    assert w.report(related_hazard_id=str(theirs))[0] is None
    assert w.report(related_hazard_id="abc")[0] is None
    assert w.report(related_hazard_id="")[0]


def test_code_format_sequence_per_month_and_collision_retry(w, monkeypatch):
    first, _ = w.report()
    second, _ = w.report()
    assert [w.db.execute("SELECT code FROM incidents WHERE id=?", (i,)).fetchone()[0] for i in (first, second)] == ["IN-2603-0001", "IN-2603-0002"]
    monkeypatch.setattr(timeutil, "today", lambda: date(2026, 4, 2))
    assert incidents.next_code(w.db) == "IN-2604-0001"
    real = incidents.next_code
    calls = []
    monkeypatch.setattr(incidents, "next_code", lambda db, today=None: calls.append(1) or ("IN-2603-0001" if len(calls) == 1 else real(db, date(2026, 3, 10))))
    third, errors = w.report()
    assert errors == [] and third and len(calls) == 2


def test_visibility_reporter_sees_only_own_investigator_sees_scope(w):
    iid, _ = w.report(user=w.rep)
    assert incidents.visible_incident(w.db, w.rep, iid) is not None
    assert incidents.visible_incident(w.db, w.rep2, iid) is None
    assert incidents.visible_incident(w.db, w.inv, iid) is not None and incidents.visible_incident(w.db, w.admin, iid) is not None
    assert incidents.visible_incident(w.db, w.inv_b, iid) is None                                       # คนละโรงงาน
    assert incidents.visible_incident(w.db, w.viewer, iid) is None                                      # master.view เห็นอุบัติเหตุไม่ได้
    assert incidents.visible_incident(w.db, w.inv, 9999) is None
    w.db.execute("UPDATE incidents SET owner_id=? WHERE id=?", (w.rep2["id"], iid)); w.db.commit()
    assert incidents.visible_incident(w.db, w.rep2, iid) is not None                                     # ผู้สอบสวนที่ได้รับมอบหมายเห็น


def test_people_visible_only_to_investigator_closer_and_own_reporter(w):
    iid, _ = w.report(user=w.rep, people_name_0="สมชาย ใจดี", people_no_0="E1")
    inc = w.db.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
    names = lambda u: [r["name"] for r in (incidents.people_for(w.db, u, inc) or [])]
    assert names(w.rep) == ["สมชาย ใจดี"] and names(w.inv) == ["สมชาย ใจดี"] and names(w.admin) == ["สมชาย ใจดี"]
    assert incidents.people_for(w.db, w.rep2, inc) is None and incidents.people_for(w.db, w.viewer, inc) is None
    closer = user_row(w.db, add_user(w.db, "closer"))
    w.db.execute("INSERT INTO safety_roles(key,name_th,permissions) VALUES('closer','ผู้ปิด','incident.close')"); w.db.commit()
    assert names(closer) == ["สมชาย ใจดี"]
    assert incidents.can_see_people(w.db, w.rep2, inc) is False


def test_event_and_notification_text_never_contain_names(w):
    iid, _ = w.report(people_name_0="สมชาย ใจดี", people_no_0="EMP-7788", people_note_0="แขนหัก")
    blobs = [" ".join(str(v) for v in tuple(r)) for t in ("incident_events", "notifications", "notify_outbox") for r in w.db.execute(f"SELECT * FROM {t}")]
    assert blobs and not any(("สมชาย" in b or "EMP-7788" in b or "แขนหัก" in b) for b in blobs)


def test_scale_size_change_later_does_not_break_old_near_miss_rows(w):
    iid, _ = w.report(kind="near_miss", likelihood="5", severity="5")
    w.db.execute("DELETE FROM risk_scales WHERE kind='likelihood' AND level=5"); w.db.commit()
    row = incidents.visible_incident(w.db, w.inv, iid)
    assert row["score"] == 25 and row["level_name"] == "สูงมาก"
    assert w.report(kind="near_miss", likelihood="5", severity="5")[0] is None                          # แจ้งใหม่ใช้เกณฑ์ปัจจุบัน


def test_five_year_limit_when_today_is_29_feb(w, monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: datetime(2028, 2, 29, 12, 0))
    assert w.report(occurred_at="2023-02-28T12:00")[0]
    assert w.report(occurred_at="2023-02-27T23:59")[0] is None
