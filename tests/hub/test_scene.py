import re

from hub.views import launcher
from tests.hub.test_launcher import login_as


def test_setup_and_launcher_show_the_three_zone_scene(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: None)
    pages = {"/setup": hub_app.test_client().get("/setup"), "/": login_as(hub_app).get("/")}
    for path, r in pages.items():
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and 'class="tz"' in html and "Plant illustration: Safety, Environment and Utility" in html, path
        assert all(w in html for w in ("<b>Safety</b>", "<b>Environment</b>", "<b>Utility</b>")) and "Safety 4.0" not in html and 'href="/static/hub/scene.css"' in html, path
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


def test_default_system_names_are_english_and_old_defaults_are_renamed(hub_app):
    from hub.db import connect
    from hub.schema import ensure_schema
    con = connect(hub_app.config["DATABASE"])
    assert con.execute("SELECT name_th FROM app_registry WHERE key='safety'").fetchone()[0] == "Safety"
    con.execute("UPDATE app_registry SET name_th='ความปลอดภัย (Safety)' WHERE key='safety'"); con.commit()
    ensure_schema(con)
    assert con.execute("SELECT name_th FROM app_registry WHERE key='safety'").fetchone()[0] == "Safety"
    con.execute("UPDATE app_registry SET name_th='ชื่อที่แอดมินตั้งเอง' WHERE key='safety'"); con.commit()
    ensure_schema(con)
    assert con.execute("SELECT name_th FROM app_registry WHERE key='safety'").fetchone()[0] == "ชื่อที่แอดมินตั้งเอง"


def test_login_card_markup_assets_and_a11y(hub_app):
    c = hub_app.test_client()
    html = c.get("/login").get_data(as_text=True)
    assert 'class="card login-card"' in html and 'name="username"' in html and 'name="password"' in html and 'type="password"' in html and 'name="_csrf"' in html
    assert html.count('aria-hidden="true"') >= 5 and "autocomplete=\"current-password\"" in html and 'src="/static/hub/login.js"' in html
    r = c.get("/static/hub/login.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript" and "prefers-reduced-motion" in r.get_data(as_text=True)
    css = c.get("/static/hub/scene.css").get_data(as_text=True)
    assert ".login-card" in css and "backdrop-filter" in css and "perspective" in css
    assert c.get("/static/hub/other.js").status_code == 404
    assert 'class="card login-card"' in c.get("/setup").get_data(as_text=True)


def test_gate_page_has_depth_layers_accent_tiles_and_assets(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: 5)
    c = login_as(hub_app)
    html = c.get("/").get_data(as_text=True)
    assert 'id="gate-stage"' in html and 'id="gate-hero"' in html and 'class="orb orb-a"' in html and 'src="/static/hub/gate.js"' in html
    tiles = re.findall(r'<a class="tile".*?</a>', html, re.S)
    assert tiles and all('data-accent="' in t and "--i:" in t and "tile-go" in t and 'class="icon"' in t and "<strong>" in t for t in tiles)
    assert 'data-accent="amber"' in tiles[0] and "รายการที่ต้องติดตาม" in tiles[0]                  # Safety = โซนความปลอดภัย (เหลือง) + ป้ายตัวเลข
    r = c.get("/static/hub/gate.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript" and "prefers-reduced-motion" in r.get_data(as_text=True)
    assert "perspective" in c.get("/static/hub/scene.css").get_data(as_text=True)


def test_login_pages_are_safety_only_with_animated_safety_scene_and_no_mascot_or_other_zones(hub_app):
    c = hub_app.test_client()
    from tests.hub.test_auth import add_user
    add_user(hub_app)
    for path in ("/login", "/register"):
        html = c.get(path).get_data(as_text=True)
        assert 'class="ss"' in html and "AIAT Safety" in html and "<b>Safety</b>" in html, path
        assert "mascot" not in html and "Environment" not in html and "Utility" not in html and 'class="tz"' not in html, path
        assert "เข้าสู่ระบบครั้งเดียว" not in html and "ใช้ได้ทุกระบบ" not in html and "ทุกระบบที่คุณมีสิทธิ์" not in html, path
        assert html.count('id="ss-glow"') == 1 and 'id="ss-gold"' in html and "Safety 4.0" not in html, path
        assert not re.search(r"<script|onload=|href=\"http", html.split('class="ss"', 1)[1].split("</svg>", 1)[0]), path
    assert 'href="/"' in c.get("/login").get_data(as_text=True)                                  # ลิงก์กลับหน้า Hub


def test_safety_scene_css_has_the_animations_and_reduced_motion_fallback(hub_app):
    css = hub_app.test_client().get("/static/hub/scene.css").get_data(as_text=True)
    for k in ("ss-orbit", "ss-ring", "ss-check", "ss-tape", "ss-shield", "ss-spark"):
        assert f".{k}" in css, k
    assert ".ss *, .ss { animation: none !important" in css
