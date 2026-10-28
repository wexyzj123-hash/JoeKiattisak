import io
import json
from datetime import date, timedelta

import pytest

from safety import stats, timeutil
from safety.db import connect
from safety.risk import list_levels
from safety.schema import ensure_schema
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_user, user_row
from tests.safety.helpers import login

TODAY = date(2026, 3, 10)
D = lambda n: (TODAY + timedelta(days=n)).isoformat()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class World:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        self.rep = add_user(db, "reporter", "ผู้แจ้ง")
        self.rep2 = add_user(db, "reporter", "ผู้แจ้ง 2")
        self.admin = user_row(db, add_user(db, "admin"))
        self.levels = {l["name_th"]: l for l in list_levels(db)}

    def hz(self, chain="a", status="reported", l=None, s=None, due=None, reporter=None, reported_at="2026-03-01 09:00:00", **kw):
        lv = None
        if l and s:
            score = l * s
            lv = next(x for x in self.levels.values() if x["min_score"] <= score <= x["max_score"])
            kw.update(likelihood=l, severity=s, score=score, risk_level_id=lv["id"], level_name=lv["name_th"],
                      level_color=lv["color"], level_requires_approval=lv["requires_approval"])
        return add_hazard(self.db, getattr(self, chain)["a"], reporter or self.rep, status=status, due_date=due, reported_at=reported_at, **kw)

    def closed_event(self, hid, at):
        self.db.execute("INSERT INTO hazard_events(hazard_id, actor_id, kind, from_status, to_status, at) VALUES(?,?,?,?,?,?)",
                        (hid, self.rep, "closed", "assigned", "closed", at))
        self.db.commit()


@pytest.fixture
def w(db):
    return World(db)


def test_counts_include_all_status_keys_even_when_zero(w):
    assert stats.counts_by_status(w.db, w.admin) == {"reported": 0, "assessed": 0, "assigned": 0, "closed": 0, "rejected": 0}
    w.hz(); w.hz(status="closed"); w.hz(status="closed")
    assert stats.counts_by_status(w.db, w.admin) == {"reported": 1, "assessed": 0, "assigned": 0, "closed": 2, "rejected": 0}


def test_open_excludes_closed_and_rejected_and_groups_by_level(w):
    w.hz(status="assessed", l=1, s=1); w.hz(status="assigned", l=1, s=2); w.hz(status="assigned", l=5, s=5)
    w.hz(status="closed", l=5, s=5); w.hz(status="rejected", l=3, s=3); w.hz()                          # ยังไม่ประเมิน 1
    got = stats.open_by_level(w.db, w.admin)
    assert got == [("ต่ำ", "#2ecc71", 2), ("สูงมาก", "#ff4d4f", 1), ("ยังไม่ประเมิน", "#8fa3bd", 1)]


def test_overdue_boundary_today_not_overdue_yesterday_overdue_and_closed_never(w):
    w.hz(status="assigned", due=D(0)); w.hz(status="assigned", due=D(-1)); w.hz(status="assigned", due=D(1))
    w.hz(status="closed", due=D(-9)); w.hz(status="rejected", due=D(-9)); w.hz(status="assessed", due=D(-30)); w.hz(status="reported")
    assert stats.overdue_count(w.db, w.admin) == 2


def test_heatmap_counts_open_assessed_only(w):
    w.hz(status="assessed", l=3, s=4); w.hz(status="assigned", l=3, s=4); w.hz(status="assigned", l=5, s=1)
    w.hz(status="closed", l=3, s=4); w.hz(status="rejected", l=2, s=2); w.hz()
    assert stats.heatmap(w.db, w.admin) == {(3, 4): 2, (5, 1): 1}


def test_trend_zero_filled_and_ordered_across_year_boundary(w, monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: date(2026, 2, 10))
    a = w.hz(reported_at="2025-12-20 10:00:00"); w.hz(reported_at="2025-12-31 23:59:59"); w.hz(reported_at="2026-02-01 00:00:00")
    w.hz(reported_at="2024-01-01 00:00:00")                                                              # นอกช่วง
    w.closed_event(a, "2026-01-15 12:00:00")
    assert stats.monthly_trend(w.db, w.admin, months=4) == [("2025-11", 0, 0), ("2025-12", 2, 0), ("2026-01", 0, 1), ("2026-02", 1, 0)]
    assert len(stats.monthly_trend(w.db, w.admin)) == 12 and stats.monthly_trend(w.db, w.admin)[-1][0] == "2026-02"


def test_scope_and_reporter_visibility_limit_all_stats(w):
    w.hz("a", status="assigned", l=5, s=5, due=D(-1)); w.hz("b", status="assigned", l=2, s=2, due=D(-1), reporter=w.rep2)
    scoped = user_row(w.db, add_user(w.db, "assessor", factory_id=w.b["f"]))
    assert stats.counts_by_status(w.db, scoped)["assigned"] == 1 and stats.overdue_count(w.db, scoped) == 1
    assert stats.open_by_level(w.db, scoped) == [("ต่ำ", "#2ecc71", 1)] and stats.heatmap(w.db, scoped) == {(2, 2): 1}
    assert stats.counts_by_status(w.db, w.admin)["assigned"] == 2
    rep2 = user_row(w.db, w.rep2)                                                                          # ผู้แจ้งเห็นเฉพาะของตัวเอง
    assert stats.counts_by_status(w.db, rep2)["assigned"] == 1 and stats.heatmap(w.db, rep2) == {(2, 2): 1}
    assert stats.monthly_trend(w.db, rep2)[-1] == ("2026-03", 1, 0)


def test_by_department_counts_includes_empty_departments(w):
    w.hz("a", status="assigned", l=5, s=5, due=D(-2)); w.hz("a", status="assessed", l=1, s=1)
    w.hz("a", status="closed", l=5, s=5); w.hz("a", status="assigned", l=4, s=4, due=D(3))
    rows = stats.by_department(w.db, w.admin)
    got = {r["department"]: (r["factory"], r["open"], r["overdue"], r["high_or_above"]) for r in rows}
    assert got == {"แผนก A": ("โรงงาน A", 3, 1, 1), "แผนก B": ("โรงงาน B", 0, 0, 0)}
    scoped = user_row(w.db, add_user(w.db, "assessor", factory_id=w.b["f"]))
    assert [r["department"] for r in stats.by_department(w.db, scoped)] == ["แผนก B"]


def test_global_summary_ignores_scope(w):
    w.hz(status="assigned", due=D(-1)); w.hz("b", status="reported"); w.hz(status="closed")
    assert stats.global_summary(w.db) == (2, 1)


# ---------- หน้าจอ + API ----------
def test_overview_pages_render_and_require_login_and_permission(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    rep = login(safety_app, "reporter")
    add_hazard(con, ch["a"], rep.uid, status="assigned", due_date=D(-1), likelihood=5, severity=5, score=25, level_name="สูงมาก",
               level_color="#ff4d4f", level_requires_approval=1)
    for path in ("/overview", "/overview/departments"):
        assert safety_app.test_client().get(path).status_code == 302
        r = login(safety_app, "assessor").get(path)
        assert r.status_code == 200
    html = login(safety_app, "assessor").get("/overview").get_data(as_text=True)
    assert "เกินกำหนด" in html and "สูงมาก" in html and "#ff4d4f" in html and "2026-03" in html
    assert "แผนก 1" in login(safety_app, "assessor").get("/overview/departments").get_data(as_text=True)
    admin = login(safety_app, "admin")
    admin.post("/admin/roles", data={"_csrf": csrf(admin), "action": "create", "key": "hr_only", "name_th": "x", "permissions": ["users.manage"]})
    assert login(safety_app, "hr_only").get("/overview").status_code == 403


def test_overview_heatmap_uses_level_colors_and_counts(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    rep = login(safety_app, "reporter")
    for _ in range(2):
        add_hazard(con, ch["a"], rep.uid, status="assessed", likelihood=1, severity=1, score=1, level_name="ต่ำ", level_color="#2ecc71")
    html = login(safety_app, "assessor").get("/overview").get_data(as_text=True)
    assert 'data-cell="1-1"' in html and 'data-count="2"' in html and "#2ecc71" in html


def test_summary_api_returns_real_counts_and_requires_token(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    u = add_user(con, "reporter")
    add_hazard(con, ch["a"], u, status="assigned", due_date=D(-1)); add_hazard(con, ch["a"], u, status="reported")
    add_hazard(con, ch["a"], u, status="closed", due_date=D(-9))
    c = safety_app.test_client()
    assert c.get("/api/summary").status_code == 401
    r = c.get("/api/summary", headers={"X-Service-Token": "svc-token"})
    assert r.get_json() == {"open_hazards": 2, "overdue": 1}


def test_hub_badge_sums_summary(safety_app, monkeypatch):
    from hub.views import launcher
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    u = add_user(con, "reporter")
    add_hazard(con, ch["a"], u, status="assigned", due_date=D(-1)); add_hazard(con, ch["a"], u, status="reported")
    body = safety_app.test_client().get("/api/summary", headers={"X-Service-Token": "svc-token"}).get_data()

    class Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(launcher, "urlopen", lambda req, timeout: Resp(body))
    assert launcher.fetch_badge({"key": "safety", "url": "http://safety.test"}, "svc-token") == 3          # เปิด 2 + เกินกำหนด 1


# ---------- แดชบอร์ดหน้าหลัก ----------
def _type(db, code, name):
    return db.execute("INSERT INTO hazard_types(code,name_th) VALUES(?,?)", (code, name)).lastrowid


def test_open_by_type_counts_buckets_top5_and_excludes_closed_and_zero(w):
    ids = [_type(w.db, f"X{i}", f"ประเภท{i}") for i in range(7)]
    for i, tid in enumerate(ids):
        for _ in range(7 - i):                                               # 7,6,5,4,3,2,1
            w.hz(status="assigned", hazard_type_id=tid)
    w.hz(status="closed", hazard_type_id=ids[6]); w.hz(status="rejected", hazard_type_id=ids[6])
    _type(w.db, "ZERO", "ไม่มีรายการ")
    got = stats.open_by_type(w.db, w.admin)
    assert got == [("ประเภท0", 7), ("ประเภท1", 6), ("ประเภท2", 5), ("ประเภท3", 4), ("ประเภท4", 3), ("อื่น ๆ", 3)]
    assert sum(n for _, n in got) == 28


def test_open_by_type_small_sets_have_no_other_bucket_and_scope_applies(w):
    w.hz("a", status="reported"); w.hz("b", status="assessed", reporter=w.rep2)
    assert stats.open_by_type(w.db, w.admin) == [("สารเคมี", 2)]
    scoped = user_row(w.db, add_user(w.db, "assessor", factory_id=w.b["f"]))
    assert stats.open_by_type(w.db, scoped) == [("สารเคมี", 1)]
    assert stats.open_by_type(w.db, user_row(w.db, w.rep2)) == [("สารเคมี", 1)]
    assert stats.open_by_type(w.db, user_row(w.db, add_user(w.db, "reporter", "ว่าง"))) == []


def test_closed_this_month_uses_event_month_boundaries_and_scope(w):
    a, b = w.hz(status="closed"), w.hz(status="closed")
    c, d = w.hz("b", status="closed", reporter=w.rep2), w.hz(status="closed")
    w.closed_event(a, "2026-03-01 00:00:00"); w.closed_event(b, "2026-03-10 23:59:59")
    w.closed_event(c, "2026-03-05 10:00:00"); w.closed_event(d, "2026-02-28 23:59:59")
    assert stats.closed_this_month(w.db, w.admin) == 3
    assert stats.closed_this_month(w.db, w.admin, date(2026, 2, 5)) == 1
    assert stats.closed_this_month(w.db, w.admin, date(2026, 4, 1)) == 0
    scoped = user_row(w.db, add_user(w.db, "assessor", factory_id=w.b["f"]))
    assert stats.closed_this_month(w.db, scoped) == 1
    assert stats.closed_this_month(w.db, user_row(w.db, w.rep2)) == 1


def test_high_risk_open_uses_snapshot_flag_and_ignores_closed(w):
    w.hz(status="assigned", l=5, s=5); w.hz(status="assessed", l=5, s=4); w.hz(status="closed", l=5, s=5)
    w.hz(status="assigned", l=1, s=1); w.hz()
    assert stats.high_risk_open(w.db, w.admin) == 2
    w.db.execute("UPDATE risk_levels SET requires_approval = 0")                    # แก้เกณฑ์ภายหลัง ไม่กระทบ snapshot เดิม
    w.db.commit()
    assert stats.high_risk_open(w.db, w.admin) == 2


def test_latest_hazards_order_limit_overdue_flag_and_scope(w):
    for i in range(8):
        w.hz(reported_at=f"2026-03-0{i + 1} 09:00:00", code=f"HZ-L-{i}", status="assigned", due=D(-1) if i == 7 else D(5))
    w.hz("b", reported_at="2026-03-09 09:00:00", code="HZ-L-B", reporter=w.rep2, status="closed", due=D(-3))
    rows = stats.latest_hazards(w.db, w.admin)
    assert [r["code"] for r in rows] == ["HZ-L-B", "HZ-L-7", "HZ-L-6", "HZ-L-5", "HZ-L-4", "HZ-L-3"]
    assert [bool(r["overdue"]) for r in rows] == [False, True, False, False, False, False]          # ปิดแล้วไม่เกินกำหนด
    assert len(stats.latest_hazards(w.db, w.admin, limit=2)) == 2
    scoped = user_row(w.db, add_user(w.db, "assessor", factory_id=w.b["f"]))
    assert [r["code"] for r in stats.latest_hazards(w.db, scoped)] == ["HZ-L-B"]
    assert {"area_name", "level_name", "level_color", "status", "due_date", "title"} <= set(rows[0].keys())
