import re

import pytest

from hub.views import launcher
from tests.hub.test_launcher import login_as


def test_login_and_setup_render_the_mascot_scene(hub_app):
    c = hub_app.test_client()
    for path in ("/setup", "/login"):
        html = c.get(path).get_data(as_text=True)
        assert 'role="img"' in html and "mascot-default" in html and "AIAT" in html, path


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
