import re
from datetime import date

import pytest

from safety import charts, timeutil
from safety.db import connect
from tests.safety.factories import add_chain, add_hazard, add_user
from tests.safety.helpers import login

TODAY = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)


def kpi(html, name):
    return int(re.search(rf'data-kpi="{name}">(\d+)<', html).group(1))


def seed(app):
    con = connect(app.config["DATABASE"])
    a, b = add_chain(con, "A"), add_chain(con, "B")
    rep = add_user(con, "reporter", "ผู้แจ้ง")
    lv = con.execute("SELECT * FROM risk_levels ORDER BY max_score DESC LIMIT 1").fetchone()
    snap = dict(likelihood=5, severity=5, score=25, risk_level_id=lv["id"], level_name=lv["name_th"], level_color=lv["color"],
                level_requires_approval=1)
    h1 = add_hazard(con, a["a"], rep, code="HZ-D-1", status="assigned", due_date="2026-03-01", reported_at="2026-03-02 09:00:00", **snap)
    add_hazard(con, a["a"], rep, code="HZ-D-2", status="reported", reported_at="2026-03-05 09:00:00")
    h3 = add_hazard(con, b["a"], rep, code="HZ-D-3", status="closed", reported_at="2026-02-20 09:00:00")
    con.execute("INSERT INTO hazard_events(hazard_id, actor_id, kind, from_status, to_status, at) VALUES(?,?,?,?,?,?)",
                (h3, rep, "closed", "assigned", "closed", "2026-03-04 10:00:00"))
    con.commit()
    con.close()
    return a, b, rep, h1


def test_kpis_match_database_for_admin_scoped_assessor_and_reporter(safety_app):
    a, b, rep, _ = seed(safety_app)
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    assert (kpi(html, "open"), kpi(html, "overdue"), kpi(html, "closed-month"), kpi(html, "high-risk")) == (2, 1, 1, 1)
    scoped = login(safety_app, "assessor", factory_id=b["f"]).get("/").get_data(as_text=True)
    assert (kpi(scoped, "open"), kpi(scoped, "overdue"), kpi(scoped, "closed-month"), kpi(scoped, "high-risk")) == (0, 0, 1, 0)
    other = login(safety_app, "reporter").get("/").get_data(as_text=True)                  # ผู้แจ้งอีกคนไม่เห็นของคนอื่น
    assert (kpi(other, "open"), kpi(other, "closed-month")) == (0, 0) and "HZ-D-1" not in other


def test_dashboard_renders_with_empty_database_without_errors(safety_app):
    r = login(safety_app, "admin").get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and (kpi(html, "open"), kpi(html, "overdue")) == (0, 0)
    assert "ยังไม่มีรายการอันตราย" in html and "nan" not in html.lower().replace("fanatic", "") and "ZeroDivision" not in html
    assert "mascot-sleeping" in html


def test_donut_percentages_sum_and_single_slice_and_empty():
    svg, legend = charts.donut([("ก", 1), ("ข", 1), ("ค", 1)])
    assert sum(l["pct"] for l in legend) in (99, 100, 101) and svg.count("<path") == 3
    one, leg = charts.donut([("เดียว", 4)])
    assert "<circle" in one and leg[0]["pct"] == 100 and "<path d=\"M" not in one
    empty, leg2 = charts.donut([])
    assert leg2 == [] and ">0<" in empty


def test_chart_labels_are_escaped():
    svg, _ = charts.donut([("<script>x</script>", 2), ("ข", 1)])
    assert "<script>" not in svg
    t = charts.trend([("2026-03<", 1, 2)])
    assert "<script" not in t and "&lt;" in t


def test_trend_svg_has_summary_and_hidden_table_with_12_months(safety_app):
    seed(safety_app)
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    assert 'role="img"' in html and "แนวโน้มแจ้ง/ปิด 12 เดือน" in html
    table = html[html.index('class="sr-only"'):]
    assert len(re.findall(r"<tr><td>20\d\d-\d\d</td>", table)) == 12


def test_donut_and_latest_table_show_real_rows(safety_app):
    seed(safety_app)
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    assert "สารเคมี" in html and "HZ-D-1" in html and "HZ-D-2" in html and "เกินกำหนด" in html
    assert html.index("HZ-D-2") < html.index("HZ-D-1")                                       # ใหม่สุดก่อน


def test_kpi_links_apply_filters(safety_app):
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    assert 'href="/hazards?status=closed"' in html and "sort=due_date&amp;dir=asc" in html and "sort=score&amp;dir=desc" in html


def test_quick_menu_only_permitted_items(safety_app):
    viewer = login(safety_app, "viewer").get("/").get_data(as_text=True)
    quick = viewer[viewer.index('class="quick"'):viewer.index('id="h-notes"')]
    assert "/master/factories" in quick and "/admin/users" not in quick and 'href="/"' not in quick
    admin = login(safety_app, "admin").get("/").get_data(as_text=True)
    assert "/admin/users" in admin[admin.index('class="quick"'):]


def test_my_notifications_only_own_latest_five(safety_app):
    me = login(safety_app, "reporter")
    other = login(safety_app, "reporter")
    con = connect(safety_app.config["DATABASE"])
    for i in range(7):
        con.execute("INSERT INTO notifications(user_id,event,title,dedupe_key,created_at) VALUES(?,?,?,?,?)",
                    (me.uid, "hazard_assigned", f"ของฉัน {i}", f"k{i}", f"2026-03-0{i + 1} 10:00:00"))
    con.execute("INSERT INTO notifications(user_id,event,title,dedupe_key) VALUES(?,?,?,?)", (other.uid, "x", "ของคนอื่น", "z"))
    con.commit(); con.close()
    html = me.get("/").get_data(as_text=True)
    section = html[html.index('id="h-notes"'):]
    assert "ของฉัน 6" in section and "ของฉัน 2" in section and "ของฉัน 1<" not in section and "ของคนอื่น" not in section
    assert section.count('class="note-row') == 5 and "/notifications/go/" in section


def test_user_text_escaped_everywhere(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "E")
    rep = add_user(con, "reporter", "<b>ชื่อ</b>")
    add_hazard(con, ch["a"], rep, code="HZ-E-1", title="<img src=x onerror=alert(1)>")
    con.execute("INSERT INTO notifications(user_id,event,title,dedupe_key) VALUES(?,?,?,?)", (rep, "x", "<script>alert(2)</script>", "e"))
    con.commit(); con.close()
    c = safety_app.test_client()
    with c.session_transaction() as s:
        s["suid"] = rep
    html = c.get("/").get_data(as_text=True)
    assert "<img src=x" not in html and "<script>alert(2)" not in html and "<b>ชื่อ</b>" not in html and "&lt;img" in html


def test_query_count_constant_with_many_hazards(safety_app):
    def run(n):
        con = connect(safety_app.config["DATABASE"])
        ch = add_chain(con, f"Q{n}")
        rep = add_user(con, "reporter", f"คน{n}")
        for i in range(n):
            add_hazard(con, ch["a"], rep, code=f"HZ-Q{n}-{i}")
        con.close()
        c = login(safety_app, "admin")
        import sqlite3
        statements = []
        orig = sqlite3.Connection.set_trace_callback
        from safety import db as dbmod
        real_connect = dbmod.connect

        def traced(path):
            con2 = real_connect(path)
            con2.set_trace_callback(statements.append)
            return con2
        dbmod.connect = traced
        try:
            c.get("/")
        finally:
            dbmod.connect = real_connect
        return len([s for s in statements if s.strip().upper().startswith("SELECT")])
    small, large = run(3), run(150)
    assert small == large and small > 0


def test_no_widgets_without_real_data(safety_app):
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    for fake in ("สภาพอากาศ", "e-Learning", "ข่าวสาร", "วันปลอดอุบัติเหตุ"):
        assert fake not in html
