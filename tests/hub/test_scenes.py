import re

import pytest

from hub.views import launcher
from tests.hub.test_launcher import login_as


def test_login_and_setup_render_the_mascot_scene(hub_app):
    c = hub_app.test_client()
    for path in ("/setup", "/login"):
        html = c.get(path).get_data(as_text=True)
        assert "mascot-default" in html and "Safety 4.0" in html and 'class="wordmark"' in html and "AIAT" in html, path
        assert 'class="auth-split"' in html and 'aria-hidden="true"' in html, path


@pytest.mark.parametrize("badge, mood", [(0, "default"), (None, "default"), (4, "alert")])
def test_launcher_mascot_is_alert_only_when_a_badge_is_positive(hub_app, monkeypatch, badge, mood):
    monkeypatch.setattr(launcher, "fetch_badge", lambda row, token, timeout=2.0: badge)
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert f"mascot-{mood}" in html
    other = "alert" if mood == "default" else "default"
    assert f"mascot-{other}" not in html
    assert "สวัสดี" in html


def test_launcher_escapes_user_name_next_to_mascot(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: None)
    from tests.hub.test_auth import add_user
    from hub.db import connect
    con = connect(hub_app.config["DATABASE"])
    add_user(hub_app, "x1", role="admin")
    con.execute("UPDATE users SET name_th=?", ('<script>alert(1)</script>',)); con.commit()
    c = hub_app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username='x1'").fetchone()[0]
    with c.session_transaction() as s:
        s["uid"] = uid
    html = c.get("/").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_theme_assets_served_and_linked_by_hub(hub_app):
    c = hub_app.test_client()
    css, js = c.get("/static/theme.css"), c.get("/static/theme.js")
    assert css.status_code == js.status_code == 200 and "css" in css.mimetype and "javascript" in js.mimetype
    assert "aiat-theme" in js.get_data(as_text=True) and "try" in js.get_data(as_text=True)
    html = c.get("/login").get_data(as_text=True)
    assert '/static/theme.js' in html and "data-theme-toggle" in html


def test_launcher_has_theme_toggle(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: None)
    assert "data-theme-toggle" in login_as(hub_app).get("/").get_data(as_text=True)


def test_launcher_tiles_show_icon_name_and_badge(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda row, token, timeout=2.0: 7)
    html = login_as(hub_app).get("/").get_data(as_text=True)
    tiles = re.findall(r'<a class="tile".*?</a>', html, re.S)
    assert tiles and all('class="icon"' in t and "<strong>" in t for t in tiles)
    assert any('class="badge"' in t and ">7<" in t for t in tiles)


def test_launcher_user_menu_has_logout_with_csrf_and_labelled_icon_buttons(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_badge", lambda *a, **k: None)
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert '<details class="user-menu">' in html and 'action="/logout"' in html and 'name="_csrf"' in html
    for tag in re.findall(r"<button\b[^>]*>(?:(?!</button>).)*</button>", html, re.S):
        text = re.sub(r"<svg.*?</svg>|<[^>]+>", "", tag, flags=re.S).strip()
        assert text or "aria-label" in tag.split(">")[0], tag[:120]
    assert "Safety 4.0" in html and re.search(r"<summary aria-label=\"เมนูผู้ใช้", html)


def test_hub_login_has_visible_labelled_fields_and_no_fixed_toggle_overlap(hub_app):
    html = hub_app.test_client().get("/login").get_data(as_text=True)
    assert "ชื่อผู้ใช้" in html and "รหัสผ่าน" in html and 'class="theme-toggle theme-fixed"' in html
