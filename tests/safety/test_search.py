import pytest

from safety.db import connect
from safety.schema import ensure_schema
from safety.search import search_hazards
from tests.safety.factories import add_chain, add_hazard, add_user, user_row
from tests.safety.helpers import login


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


@pytest.fixture
def world(db):
    a, b = add_chain(db, "A"), add_chain(db, "B")
    rep = add_user(db, "reporter", "ผู้แจ้ง")
    other = add_user(db, "reporter", "อีกคน")
    admin = user_row(db, add_user(db, "admin"))
    return db, a, b, rep, other, admin


def test_matches_code_and_title_case_insensitive_and_thai(world):
    db, a, b, rep, other, admin = world
    add_hazard(db, a["a"], rep, code="HZ-2603-0001", title="น้ำมันรั่วที่พื้น")
    add_hazard(db, a["a"], rep, code="HZ-2603-0002", title="Forklift Speeding")
    assert [r["code"] for r in search_hazards(db, admin, "น้ำมัน")] == ["HZ-2603-0001"]
    assert [r["code"] for r in search_hazards(db, admin, "forklift")] == ["HZ-2603-0002"]
    assert [r["code"] for r in search_hazards(db, admin, "hz-2603-0001")] == ["HZ-2603-0001"]


@pytest.mark.parametrize("q", ["%", "_", "\\", "%%", "a_b"])
def test_wildcards_are_literal(world, q):
    db, a, b, rep, other, admin = world
    add_hazard(db, a["a"], rep, title="ปกติ ไม่มีสัญลักษณ์")
    assert search_hazards(db, admin, q) == []
    add_hazard(db, a["a"], rep, title=f"มี {q} อยู่")
    assert len(search_hazards(db, admin, q)) == 1


def test_query_trimmed_capped_and_empty_returns_nothing(world):
    db, a, b, rep, other, admin = world
    add_hazard(db, a["a"], rep, title="ท่อรั่ว")
    assert search_hazards(db, admin, "   ") == [] and search_hazards(db, admin, "") == [] and search_hazards(db, admin, None) == []
    assert len(search_hazards(db, admin, "  ท่อ  ")) == 1
    assert search_hazards(db, admin, "ท่อรั่ว" + "x" * 5000) == []            # ตัดที่ 100 ตัวอักษร แล้วไม่พบเพราะมี x ต่อท้าย
    add_hazard(db, a["a"], rep, title="y" * 100)
    assert len(search_hazards(db, admin, "y" * 100 + "zzz")) == 1             # ส่วนเกิน 100 ถูกตัดทิ้ง


def test_limit_50_newest_first(world):
    db, a, b, rep, other, admin = world
    for i in range(60):
        add_hazard(db, a["a"], rep, code=f"HZ-X-{i:04d}", title="ซ้ำ", reported_at=f"2026-01-01 00:{i // 60:02d}:{i % 60:02d}")
    rows = search_hazards(db, admin, "ซ้ำ")
    assert len(rows) == 50 and rows[0]["code"] == "HZ-X-0059" and rows[-1]["code"] == "HZ-X-0010"


def test_scope_and_reporter_visibility_enforced(world):
    db, a, b, rep, other, admin = world
    add_hazard(db, a["a"], rep, code="HZ-A-1", title="เหมือนกัน")
    add_hazard(db, b["a"], rep, code="HZ-B-1", title="เหมือนกัน")
    add_hazard(db, a["a"], other, code="HZ-A-2", title="เหมือนกัน")
    scoped = user_row(db, add_user(db, "admin", "ในโรงงาน A", factory_id=a["f"]))
    assert sorted(r["code"] for r in search_hazards(db, scoped, "เหมือน")) == ["HZ-A-1", "HZ-A-2"]
    assert sorted(r["code"] for r in search_hazards(db, scoped, "HZ-B")) == []
    reporter = user_row(db, rep)
    assert sorted(r["code"] for r in search_hazards(db, reporter, "เหมือน")) == ["HZ-A-1", "HZ-B-1"]   # เฉพาะที่ตนแจ้ง
    assert search_hazards(db, reporter, "HZ-A-2") == []


def test_route_requires_login_and_permission(safety_app):
    assert safety_app.test_client().get("/search?q=x").status_code == 302
    assert login(safety_app, "no-such-role").get("/search?q=x").status_code == 403         # บทบาทที่ไม่มีสิทธิ์ใด ๆ
    assert login(safety_app, "viewer").get("/search?q=x").status_code == 200
    assert login(safety_app, "reporter").get("/search?q=x").status_code == 200


def test_route_renders_results_escaped_and_hides_unauthorised(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "Z")
    rep = add_user(con, "reporter", "ผู้แจ้ง")
    add_hazard(con, ch["a"], rep, code="HZ-S-1", title="<script>alert(1)</script> ค้นหาได้")
    con.close()
    html = login(safety_app, "admin").get("/search?q=ค้นหาได้").get_data(as_text=True)
    assert "HZ-S-1" in html and "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert "พบ 1 รายการ" in html


def test_route_empty_and_no_result_states(safety_app):
    c = login(safety_app, "admin")
    empty = c.get("/search").get_data(as_text=True)
    assert 'name="q"' in empty and "พบ " not in empty
    none = c.get("/search?q=ไม่มีแน่นอน123").get_data(as_text=True)
    assert "ไม่พบรายการ" in none and "mascot-thinking" in none and 'aria-hidden="true"' in none
    long_q = c.get("/search?q=" + "ก" * 5000)
    assert long_q.status_code == 200


def test_reporter_search_is_limited_to_own_hazards(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "Y")
    mine = login(safety_app, "reporter")
    other = add_user(con, "reporter", "คนอื่น")
    add_hazard(con, ch["a"], mine.uid, code="HZ-M-1", title="ของฉัน ร่วม")
    add_hazard(con, ch["a"], other, code="HZ-O-1", title="ของคนอื่น ร่วม")
    con.close()
    html = mine.get("/search?q=ร่วม").get_data(as_text=True)
    assert "HZ-M-1" in html and "HZ-O-1" not in html
