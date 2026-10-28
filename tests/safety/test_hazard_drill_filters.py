from datetime import date

import pytest

from safety import timeutil
from safety.db import connect
from tests.safety.factories import add_chain, add_hazard, add_refs
from tests.safety.helpers import login


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: date(2026, 3, 10))


def codes(html):
    import re
    return sorted(set(re.findall(r"HZ-[A-Z0-9-]+", html)))


@pytest.fixture
def env(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "A")
    t1, s1 = add_refs(con)
    t2 = con.execute("INSERT INTO hazard_types(code,name_th) VALUES('T2','ไฟฟ้า')").lastrowid
    con.commit()
    c = login(safety_app, "admin")
    add_hazard(con, ch["a"], c.uid, code="HZ-AAA-1", reported_at="2026-03-02 08:00", likelihood=2, severity=3, score=6)
    add_hazard(con, ch["a"], c.uid, code="HZ-BBB-2", reported_at="2026-02-05 08:00", hazard_type_id=t2, likelihood=4, severity=4, score=16)
    con.commit()
    return type("E", (), dict(c=c, t1=t1, t2=t2))


def test_filter_by_type_month_likelihood_severity(env):
    got = lambda q: codes(env.c.get("/hazards?" + q).get_data(as_text=True))
    assert got(f"type={env.t2}") == ["HZ-BBB-2"] and got(f"type={env.t1}") == ["HZ-AAA-1"]
    assert got("month=2026-03") == ["HZ-AAA-1"] and got("month=2026-02") == ["HZ-BBB-2"]
    assert got("l=4&s=4") == ["HZ-BBB-2"] and got("l=2&s=3") == ["HZ-AAA-1"] and got("l=5&s=5") == []
    assert got("month=2026-13") == ["HZ-AAA-1", "HZ-BBB-2"] and got("type=abc") == ["HZ-AAA-1", "HZ-BBB-2"]          # ค่าผิดรูปแบบ = ไม่กรอง


def test_filters_survive_sorting_and_paging_links(env):
    html = env.c.get(f"/hazards?type={env.t2}&month=2026-02&l=4&s=4").get_data(as_text=True)
    assert f"type={env.t2}" in html and "month=2026-02" in html and "l=4" in html and "s=4" in html
