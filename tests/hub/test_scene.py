import re

from hub.views import launcher
from tests.hub.test_launcher import login_as


def test_login_setup_and_launcher_show_the_three_zone_scene(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: None)
    pages = {"/login": hub_app.test_client().get("/login"), "/setup": hub_app.test_client().get("/setup"), "/": login_as(hub_app).get("/")}
    for path, r in pages.items():
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and 'class="tz"' in html and "ภาพประกอบโรงงานสามโซน" in html, path
        assert all(w in html for w in ("ความปลอดภัย", "สิ่งแวดล้อม", "Utility")) and 'href="/static/hub/scene.css"' in html, path
        assert html.count('id="tz-sky"') == 1                                                       # ไม่มี id ซ้ำในหน้าเดียว
        assert not re.search(r"<script|onload=|href=\"http", html.split('class="tz"', 1)[1].split("</svg>", 1)[0]), path     # ภาพล้วน ไม่มีสคริปต์/ลิงก์ภายนอก


def test_scene_css_is_public_and_respects_reduced_motion(hub_app):
    r = hub_app.test_client().get("/static/hub/scene.css")
    css = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/css" and "prefers-reduced-motion: reduce" in css and "animation: none" in css
    assert hub_app.test_client().get("/static/hub/other.css").status_code == 404


def test_launcher_keeps_greeting_mascot_and_tiles(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: 3)
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert "สวัสดี" in html and "mascot-alert" in html and 'class="tile"' in html and "เลือกระบบที่ต้องการใช้งาน" in html
