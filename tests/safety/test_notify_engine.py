import pytest

from safety import notify
from safety.db import connect
from safety.notify import emit, recipients_for, render_message, valid_contact
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_user, user_row


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class N:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        self.rep = add_user(db, "reporter", "ผู้แจ้ง")
        self.owner = add_user(db, "assessor", "เจ้าของ")
        self.asr_a = add_user(db, "assessor", "ผู้ประเมิน A", factory_id=self.a["f"])
        self.asr_b = add_user(db, "assessor", "ผู้ประเมิน B", factory_id=self.b["f"])
        self.asr_all = add_user(db, "assessor", "ผู้ประเมินทั้งหมด")
        self.head = add_user(db, "dept_head", "หัวหน้า")
        self.admin = add_user(db, "admin", "แอดมิน")

    def hz(self, chain="a", **kw):
        return add_hazard(self.db, getattr(self, chain)["a"], self.rep, **{"title": "น้ำมันหก", **kw})

    def row(self, hid):
        return self.db.execute("SELECT * FROM hazards WHERE id=?", (hid,)).fetchone()

    def rule(self, event, **kw):
        return self.db.execute("SELECT * FROM notify_rules WHERE event=? AND offset_days=?", (event, kw.get("offset", 0))).fetchone()

    def notes(self, uid=None):
        q, a = "SELECT * FROM notifications", ()
        if uid:
            q, a = q + " WHERE user_id=?", (uid,)
        return self.db.execute(q + " ORDER BY id", a).fetchall()


@pytest.fixture
def n(db):
    return N(db)


def ids(rows):
    return {r["id"] for r in rows}


def test_reporter_and_owner_recipients(n):
    hid = n.hz(owner_id=n.owner)
    rule = {"recipients": "reporter,owner"}
    assert ids(recipients_for(n.db, rule, n.row(hid), None)) == {n.rep, n.owner}


def test_role_recipients_respect_scope(n):
    hid = n.hz("a")
    got = ids(recipients_for(n.db, {"recipients": "role:assessor"}, n.row(hid), None))
    assert got == {n.owner, n.asr_a, n.asr_all} and n.asr_b not in got


def test_actor_never_notified(n):
    hid = n.hz(owner_id=n.owner)
    assert ids(recipients_for(n.db, {"recipients": "reporter,owner"}, n.row(hid), n.rep)) == {n.owner}


def test_inactive_user_and_unknown_role_ignored_and_deduplicated(n):
    inactive = add_user(n.db, "assessor", "ปิดแล้ว", active=0)
    hid = n.hz(owner_id=inactive)
    assert ids(recipients_for(n.db, {"recipients": "owner,role:ghost,role:assessor,role:assessor"}, n.row(hid), None)) == \
        {n.owner, n.asr_a, n.asr_all}
    assert inactive not in ids(recipients_for(n.db, {"recipients": "owner"}, n.row(hid), None))


def test_owner_unset_yields_no_recipient(n):
    assert recipients_for(n.db, {"recipients": "owner"}, n.row(n.hz()), None) == []


def test_emit_creates_notification_with_link_and_thai_text(n):
    hid = n.hz()
    assert emit(n.db, "hazard_reported", hid, n.rep) == 3                    # assessor ในขอบเขต 3 คน
    note = n.notes(n.asr_a)[0]
    assert note["link"] == f"/hazards/{hid}" and note["event"] == "hazard_reported" and note["hazard_id"] == hid
    assert "แจ้งอันตราย" in note["title"] and "น้ำมันหก" in note["body"] and note["read_at"] is None
    assert not n.notes(n.asr_b)


def test_emit_twice_is_idempotent(n):
    hid = n.hz()
    assert emit(n.db, "hazard_reported", hid, n.rep) == 3
    before = (len(n.notes()), n.db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0])
    assert emit(n.db, "hazard_reported", hid, n.rep) == 0
    assert (len(n.notes()), n.db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0]) == before


@pytest.mark.parametrize("score, expected", [(16, 0), (17, 2), (25, 2)])
def test_min_score_filters_assessed_rule(n, score, expected):
    hid = n.hz(score=score, status="assessed", level_name="x", level_color="#fff")
    assert emit(n.db, "hazard_assessed", hid, n.asr_all) == expected                 # dept_head + admin


def test_assessed_without_score_does_not_match_min_score_rule(n):
    assert emit(n.db, "hazard_assessed", n.hz(), None) == 0


def set_contacts(db, uid, **kw):
    cols = ", ".join(f"{k}=?" for k in kw)
    db.execute(f"UPDATE safety_users SET {cols} WHERE id=?", [*kw.values(), uid]); db.commit()


def outbox(db):
    return [tuple(r) for r in db.execute("SELECT channel, target, urgent FROM notify_outbox ORDER BY id")]


def test_external_rows_only_for_opted_in_users_with_valid_contact(n):
    set_contacts(n.db, n.owner, notify_channels="inapp,telegram,line,email", telegram_chat_id="123456",
                 line_user_id="U" + "a" * 32, email="owner@example.com")
    bad = add_user(n.db, "assessor", "ติดต่อเสีย")
    set_contacts(n.db, bad, notify_channels="inapp,telegram,email", telegram_chat_id="abc", email="no-at-sign")
    nooptin = add_user(n.db, "assessor", "ไม่สมัคร")
    set_contacts(n.db, nooptin, telegram_chat_id="999")                              # มี contact แต่ไม่ได้เลือกช่องทาง
    hid = n.hz(owner_id=n.owner)
    emit(n.db, "hazard_assigned", hid, n.asr_all)                                     # owner: inapp,telegram,line,email
    assert sorted(outbox(n.db)) == [("email", "owner@example.com", 0), ("line", "U" + "a" * 32, 0), ("telegram", "123456", 0)]
    emit(n.db, "hazard_reported", hid, n.rep)                                         # assessor: inapp,telegram
    chats = [r for r in outbox(n.db) if r[0] == "telegram"]
    assert ("telegram", "999", 0) not in chats and not any(r[1] == "abc" for r in chats)


@pytest.mark.parametrize("channel, good, bad", [
    ("telegram", ["123", "-100123456", "0"], ["", "abc", "12 3", "1" * 21, "12\n3"]),
    ("line", ["U" + "0" * 32, "U" + "f" * 32], ["", "u" + "0" * 32, "U" + "0" * 31, "U" + "G" * 32]),
    ("email", ["a@b.co", "นาย@example.com"], ["", "ab", "a@@b", "a b@c.d", "a@b.c\r\nBcc: x@y.z", "@b.c", "a@", "x" * 250 + "@b.cd"]),
])
def test_valid_contact(channel, good, bad):
    field = {"telegram": "telegram_chat_id", "line": "line_user_id", "email": "email"}[channel]
    for g in good:
        assert valid_contact(channel, {field: g}) == g
    for b in bad:
        assert valid_contact(channel, {field: b}) is None
    assert valid_contact(channel, {field: None}) is None


def test_urgent_flag_for_requires_approval_level_and_late_overdue(n):
    set_contacts(n.db, n.owner, notify_channels="inapp,telegram", telegram_chat_id="1")
    plain = n.hz(owner_id=n.owner, level_requires_approval=0)
    emit(n.db, "due_soon", plain, None, offset_days=2)
    assert outbox(n.db) == [("telegram", "1", 0)]
    strict = n.hz(owner_id=n.owner, level_requires_approval=1)
    emit(n.db, "due_soon", strict, None, offset_days=2)
    assert outbox(n.db)[-1] == ("telegram", "1", 1)
    late = n.hz(owner_id=n.owner)
    emit(n.db, "overdue", late, None, offset_days=1); emit(n.db, "overdue", late, None, offset_days=3)
    flags = [r[2] for r in n.db.execute("SELECT channel, target, urgent FROM notify_outbox WHERE dedupe_key LIKE 'overdue:%' ORDER BY id")]
    assert 0 in flags and 1 in flags


def test_teams_single_row_per_rule_and_hazard(n):
    n.db.execute("UPDATE notify_rules SET channels='inapp,teams' WHERE event='hazard_reported'"); n.db.commit()
    hid = n.hz()
    emit(n.db, "hazard_reported", hid, n.rep); emit(n.db, "hazard_reported", hid, n.rep)
    assert [r for r in outbox(n.db) if r[0] == "teams"] == [("teams", "teams", 0)]


def test_disabled_rule_skipped(n):
    n.db.execute("UPDATE notify_rules SET enabled=0 WHERE event='hazard_reported'"); n.db.commit()
    assert emit(n.db, "hazard_reported", n.hz(), n.rep) == 0 and n.notes() == []


def test_due_and_overdue_match_offset_only(n):
    hid = n.hz(owner_id=n.owner)
    assert emit(n.db, "overdue", hid, None, offset_days=2) == 0                       # ไม่มีกฎ offset 2
    assert emit(n.db, "overdue", hid, None, offset_days=1) == 1                       # เจ้าของ
    assert emit(n.db, "overdue", hid, None, offset_days=3) == 2                       # เจ้าของ + หัวหน้า (เจ้าของได้ข้อความขั้นใหม่)
    assert emit(n.db, "due_soon", hid, None, offset_days=2) == 1
    assert emit(n.db, "due_soon", hid, None, offset_days=5) == 0


def test_reassigned_owner_gets_new_notification_same_owner_deduped(n):
    other = add_user(n.db, "assessor", "เจ้าของใหม่")
    hid = n.hz(owner_id=n.owner, due_date="2026-04-01", status="assigned")
    assert emit(n.db, "hazard_assigned", hid, n.asr_all) == 1
    assert emit(n.db, "hazard_assigned", hid, n.asr_all) == 0
    n.db.execute("UPDATE hazards SET owner_id=? WHERE id=?", (other, hid)); n.db.commit()
    assert emit(n.db, "hazard_assigned", hid, n.asr_all) == 1
    n.db.execute("UPDATE hazards SET due_date='2026-05-01' WHERE id=?", (hid,)); n.db.commit()
    assert emit(n.db, "hazard_assigned", hid, n.asr_all) == 1                         # เลื่อนกำหนด = แจ้งใหม่


def test_failure_inside_emit_is_swallowed_and_rolled_back(n, monkeypatch, caplog):
    hid = n.hz()
    n.db.execute("UPDATE hazards SET title='ก่อน emit' WHERE id=?", (hid,))           # งานของผู้เรียกใน transaction เดียวกัน
    monkeypatch.setattr(notify, "_queue_external", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    n.db.execute("UPDATE safety_users SET telegram_chat_id='1', notify_channels='inapp,telegram' WHERE id=?", (n.asr_all,))
    assert emit(n.db, "hazard_reported", hid, n.rep) == 0
    assert n.notes() == []                                                            # แถวที่สร้างไปครึ่งทางถูก rollback
    assert n.row(hid)["title"] == "ก่อน emit"                                          # งานของผู้เรียกไม่หาย
    n.db.commit()
    assert n.row(hid)["title"] == "ก่อน emit" and "boom" in caplog.text


def test_unknown_hazard_or_event_is_harmless(n):
    assert emit(n.db, "hazard_reported", 99999, None) == 0
    assert emit(n.db, "not_an_event", n.hz(), None) == 0


def test_message_sanitised_and_truncated(n):
    hid = n.hz(title="ก\nข\r\x00ค\x1b[31m" + "ง" * 10000)
    title, body = render_message("hazard_reported", n.row(hid), {"area": "พื้นที่ \n A"})
    for text in (title, body):
        assert all(ord(c) >= 32 and ord(c) != 127 for c in text), repr(text)
    assert len(title) <= 150 and len(body) <= 500 and "ก ข" in body and "พื้นที่ A" in body
    t2, b2 = render_message("hazard_rejected", n.row(hid), {"reason": "เหตุผล\nหลายบรรทัด"})
    assert "เหตุผล หลายบรรทัด" in b2
    t3, b3 = render_message("overdue", n.row(hid), {"days": 3})
    assert "3" in t3 + b3
