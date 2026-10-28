import json

import pytest

from hub import dashboard
from hub.db import connect
from tests.hub.test_launcher import login_as

SUMMARY = {"open_hazards": 5, "overdue": 2, "high_risk_open": 0, "hazards_month": 3, "closed_month": 1, "incidents_month": {"accident": 0, "near_miss": 1},
           "days_without_lost_time": 123, "last_lost_time_date": "2026-06-27", "capa_open": 4, "capa_overdue": 1, "patrol_avg_score_30d": 88,
           "monthly": [{"m": f"2026-{i:02d}", "reported": i, "closed": i // 2, "accidents": 0, "near_miss": 0} for i in range(1, 13)]}


class Resp:
    status = 200
    def __init__(self, body): self.body = body
    def read(self): return json.dumps(self.body).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture(autouse=True)
def clear_cache():
    dashboard._cache.clear()


def test_fetch_caches_and_falls_back_to_stale_then_none(monkeypatch):
    calls = []
    def fake(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            return Resp(SUMMARY)
        raise OSError("down")
    monkeypatch.setattr(dashboard, "urlopen", fake)
    assert dashboard.fetch_safety("http://s", "tok", now=1000.0) == SUMMARY
    assert dashboard.fetch_safety("http://s", "tok", now=1010.0) == SUMMARY and len(calls) == 1             # ในช่วง TTL ไม่ยิงซ้ำ
    assert dashboard.fetch_safety("http://s", "tok", now=1100.0) == SUMMARY and len(calls) == 2             # ล่ม → ใช้ค่าเก่า
    assert dashboard.fetch_safety("http://s", "tok", now=1500.0) is None                                      # เก่าเกิน 5 นาที
    assert calls[0] == "http://s/api/dashboard"


@pytest.mark.parametrize("body", [{"x": 1}, [1, 2], "str"])
def test_fetch_rejects_bad_shape(monkeypatch, body):
    monkeypatch.setattr(dashboard, "urlopen", lambda req, timeout: Resp(body))
    assert dashboard.fetch_safety("http://s2", "t") is None


def test_spark_and_lamp_logic():
    assert dashboard.spark([]) == "" and dashboard.spark([5, 5, 5], 100, 20) == "3.0,10.0 50.0,10.0 97.0,10.0"
    pts = dashboard.spark([0, 10], 100, 20, 0).split()
    assert pts == ["0.0,20.0", "100.0,0.0"] and dashboard.spark([7], 100, 20).count(",") == 1
    assert dashboard.lamp(None) == "off" and dashboard.lamp({"high_risk_open": 1}) == "red" and dashboard.lamp({"days_without_lost_time": 0}) == "red"
    assert dashboard.lamp({"overdue": 1}) == "amber" and dashboard.lamp({"capa_overdue": 2}) == "amber" and dashboard.lamp({"overdue": 0, "capa_overdue": 0}) == "green"


def _with_safety(hub_app, monkeypatch, summary=SUMMARY):
    monkeypatch.setattr(dashboard, "fetch_safety", lambda url, token, **k: summary)


def test_dashboard_shows_safety_numbers_lamp_and_flip_digits(hub_app, monkeypatch):
    _with_safety(hub_app, monkeypatch)
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert 'data-lamp="amber"' in html and "มีงานเกินกำหนด" in html and "วันปลอดอุบัติเหตุหยุดงาน" in html
    assert html.count('class="flip-d"') == 3 and "--n: 1;" in html and "--n: 2;" in html and "--n: 3;" in html and "123 วัน" in html
    assert "นับจากเหตุล่าสุดวันที่ 2026-06-27" in html and "อันตรายที่ยังเปิดอยู่" in html and "88%" in html
    assert 'id="board"' in html and "/static/hub/dashboard.js" in html and "สวัสดี" in html


def test_dashboard_when_safety_is_down_is_calm_and_still_has_other_zones(hub_app, monkeypatch):
    _with_safety(hub_app, monkeypatch, None)
    r = login_as(hub_app).get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "ยังไม่เชื่อมต่อกับ Safety" in html and 'data-lamp="off"' in html and "Utility" in html and "สิ่งแวดล้อม" in html and "flip-d" not in html


def test_dashboard_env_and_utility_from_readings_and_empty_states(hub_app, monkeypatch):
    _with_safety(hub_app, monkeypatch)
    con = connect(hub_app.config["DATABASE"])
    con.execute("UPDATE metrics SET target=100 WHERE key='electricity'"); con.commit()
    from hub import metrics
    from datetime import date, timedelta
    d1, d2 = (date.today() - timedelta(days=40)).isoformat(), date.today().isoformat()
    metrics.save_reading(con, {"id": 1}, "electricity", d1, "90"); metrics.save_reading(con, {"id": 1}, "electricity", d2, "108")
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert "108" in html and "เป้า ≤ 100" in html and "▲ 20.0% จากครั้งก่อน" in html and 'class="dial st-warn"' in html
    assert "ยังไม่มีค่า" in html and "บันทึกค่าแรก" in html                                             # ตัวชี้วัดอื่นยังไม่มีค่า
    viewer = login_as(hub_app, "v1", "viewer").get("/").get_data(as_text=True)
    assert "บันทึกค่าแรก" not in viewer and "/admin/metrics" not in viewer                                # ผู้ดูทั่วไปไม่เห็นลิงก์บันทึกค่า


def test_fragment_endpoint_requires_login_and_returns_only_board(hub_app, monkeypatch):
    _with_safety(hub_app, monkeypatch)
    assert hub_app.test_client().get("/dashboard/fragment").status_code == 302
    r = login_as(hub_app).get("/dashboard/fragment")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "<html" not in body and "zone-safety" in body and r.headers["Cache-Control"] == "no-store"


def test_assets_whitelisted(hub_app):
    c = hub_app.test_client()
    assert c.get("/static/hub/dashboard.css").status_code == 200 and c.get("/static/hub/dashboard.js").mimetype == "text/javascript"
    assert c.get("/static/hub/../schema.py").status_code == 404 and c.get("/static/hub/other.js").status_code == 404


def test_metrics_link_only_for_admin_and_supervisor(hub_app, monkeypatch):
    _with_safety(hub_app, monkeypatch)
    assert "/admin/metrics" in login_as(hub_app, "s1", "supervisor").get("/").get_data(as_text=True)
    assert "/admin/metrics" not in login_as(hub_app, "t1", "technician").get("/").get_data(as_text=True)
