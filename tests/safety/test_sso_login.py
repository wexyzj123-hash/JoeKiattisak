import pytest

from safety.app import create_app
from safety.db import connect
from shared.sso import sign_token
from tests.conftest import SECRET, csrf


def token(roles=("admin",), uid=7, name="สมชาย ใจดี", secret=SECRET, now=None):
    return sign_token(secret, {"uid": uid, "name": name, "roles": list(roles)}, now=now)


def users(app):
    con = connect(app.config["DATABASE"])
    rows = con.execute("SELECT * FROM safety_users").fetchall()
    con.close()
    return rows


def test_valid_token_logs_in_and_provisions_admin(safety_app):
    c = safety_app.test_client()
    r = c.get("/sso?token=" + token())
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    (u,) = users(safety_app)
    assert (u["hub_uid"], u["role"], u["active"]) == (7, "admin", 1)
    assert c.get("/").status_code == 200


def test_non_admin_hub_role_becomes_reporter(safety_app):
    safety_app.test_client().get("/sso?token=" + token(roles=("supervisor",)))
    assert users(safety_app)[0]["role"] == "reporter"


def test_thai_name_stored(safety_app):
    safety_app.test_client().get("/sso?token=" + token(name="สุดา เก่งงาน"))
    assert users(safety_app)[0]["name"] == "สุดา เก่งงาน"


def test_replayed_token_403(safety_app):
    t = token()
    assert safety_app.test_client().get("/sso?token=" + t).status_code == 302
    assert safety_app.test_client().get("/sso?token=" + t).status_code == 403


def test_expired_token_403(safety_app):
    assert safety_app.test_client().get("/sso?token=" + token(now=1000.0)).status_code == 403


def test_tampered_token_403(safety_app):
    t = token()
    p, s = t.split(".")
    assert safety_app.test_client().get(f"/sso?token={p}x.{s}").status_code == 403
    assert safety_app.test_client().get("/sso?token=" + token(secret="z" * 40)).status_code == 403
    assert safety_app.test_client().get("/sso").status_code == 403
    assert users(safety_app) == []


def test_existing_user_role_not_overwritten_on_relogin(safety_app):
    safety_app.test_client().get("/sso?token=" + token())
    con = connect(safety_app.config["DATABASE"])
    con.execute("UPDATE safety_users SET role='assessor'")
    con.commit()
    con.close()
    safety_app.test_client().get("/sso?token=" + token())
    assert users(safety_app)[0]["role"] == "assessor"


def test_inactive_safety_user_403(safety_app):
    safety_app.test_client().get("/sso?token=" + token())
    con = connect(safety_app.config["DATABASE"])
    con.execute("UPDATE safety_users SET active=0")
    con.commit()
    con.close()
    assert safety_app.test_client().get("/sso?token=" + token()).status_code == 403


def test_home_redirects_to_hub_when_anonymous(safety_app):
    r = safety_app.test_client().get("/")
    assert r.status_code == 302 and r.headers["Location"] == "http://hub.test"


def test_logout_goes_through_hub(safety_app):
    c = safety_app.test_client()
    c.get("/sso?token=" + token())
    r = c.post("/logout", data={"_csrf": csrf(c)})
    assert r.status_code == 302 and r.headers["Location"] == "http://hub.test/logout"
    assert c.get("/").status_code == 302


@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_create_app_rejects_short_secret(tmp_path, secret):
    with pytest.raises(RuntimeError):
        create_app({"DATABASE": str(tmp_path / "s.db"), "SECRET_KEY": "k", "SSO_SECRET": secret,
                    "SERVICE_TOKEN": "s", "HUB_URL": "http://x"})


def test_theme_css_served(safety_app):
    r = safety_app.test_client().get("/static/theme.css")
    assert r.status_code == 200 and "--accent" in r.get_data(as_text=True)
