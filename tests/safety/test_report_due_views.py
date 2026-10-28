import pytest

from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain
from tests.safety.helpers import login


@pytest.fixture
def env(safety_app):
    con = connect(safety_app.config["DATABASE"])
    a, b = add_chain(con, "A"), add_chain(con, "B")
    con.close()
    admin = login(safety_app, "admin", name="แอดมิน")
    head = login(safety_app, "dept_head", name="หัวหน้า", factory_id=a["f"], department_id=a["d"])
    rep = login(safety_app, "reporter", name="ผู้แจ้ง")
    return type("E", (), dict(app=safety_app, a=a, b=b, admin=admin, head=head, rep=rep, con=lambda self=None: connect(safety_app.config["DATABASE"])))


def post(c, url, **data):
    return c.post(url, data={"_csrf": csrf(c), **data}, follow_redirects=True)


def test_admin_page_needs_report_manage_and_csrf(env):
    assert env.head.get("/admin/report-due").status_code == 403 and env.rep.get("/admin/report-due").status_code == 403
    assert env.admin.get("/admin/report-due").status_code == 200
    assert env.admin.post("/admin/report-due", data={"action": "save"}).status_code == 400


def test_create_toggle_and_history_flow(env):
    r = post(env.admin, "/admin/report-due", action="save", report_key="monthly", department_id=str(env.a["d"]), frequency="monthly", due_day="5")
    assert "บันทึกแล้ว" in r.get_data(as_text=True) and "ครบ" in r.get_data(as_text=True)
    con = env.con()
    rid = con.execute("SELECT id FROM report_due").fetchone()[0]
    assert "มีกำหนดส่งของรายงานนี้" in post(env.admin, "/admin/report-due", action="save", report_key="monthly", department_id=str(env.a["d"]), frequency="monthly", due_day="6").get_data(as_text=True)
    post(env.admin, "/admin/report-due", action="toggle", id=str(rid))
    assert con.execute("SELECT active FROM report_due WHERE id=?", (rid,)).fetchone()[0] == 0


def test_submit_from_report_page_records_once(env):
    r = post(env.admin, "/reports/annual/submit", year="2025", month="1", note="ส่งแล้ว")
    assert "บันทึกว่าส่งรายงานแล้ว" in r.get_data(as_text=True)
    assert "ส่งรายงานงวดนี้แล้ว" in post(env.admin, "/reports/annual/submit", year="2025", month="1").get_data(as_text=True)
    con = env.con()
    assert [tuple(x) for x in con.execute("SELECT report_key, period, note FROM report_submissions")] == [("annual", "2025", "ส่งแล้ว")]
    assert "ส่งแล้ว" in env.admin.get("/admin/report-due").get_data(as_text=True)


def test_scoped_user_cannot_submit_other_department_or_all(env):
    r = post(env.head, "/reports/monthly/submit", year="2026", month="1", department_id=str(env.b["d"]))
    assert r.status_code == 404 or "ไม่พบ" in r.get_data(as_text=True)
    assert "ต้องเลือกแผนกของตน" in post(env.head, "/reports/monthly/submit", year="2026", month="1").get_data(as_text=True)
    assert env.rep.post("/reports/monthly/submit", data={"_csrf": csrf(env.rep)}).status_code == 403
