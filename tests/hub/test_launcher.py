from urllib.parse import parse_qs, urlparse

from hub.db import connect
from hub.schema import seed_default_apps
from shared.sso import verify_token
from tests.conftest import SECRET
from tests.hub.test_auth import add_user


def sql(app, q, args=()):
    con = connect(app.config["DATABASE"])
    con.execute(q, args)
    con.commit()
    con.close()


def login_as(app, username="boss", role="admin", active=1):
    add_user(app, username=username, role=role, active=active)
    c = app.test_client()
    con = connect(app.config["DATABASE"])
    uid = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    con.close()
    with c.session_transaction() as s:
        s["uid"] = uid
    return c


def add_app(app, key, name, enabled=1, required_role=""):
    sql(app, "INSERT INTO app_registry(key,name_th,url,icon,enabled,required_role) VALUES(?,?,?,?,?,?)",
        (key, name, f"http://{key}.test", "app", enabled, required_role))


def test_launcher_requires_login(hub_app):
    assert hub_app.test_client().get("/").status_code == 302


def test_launcher_lists_enabled_apps_only(hub_app):
    add_app(hub_app, "off", "ระบบปิดอยู่", enabled=0)
    add_app(hub_app, "on", "ระบบเปิดอยู่")
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert "ความปลอดภัย (Safety)" in html and "ระบบเปิดอยู่" in html
    assert "ระบบปิดอยู่" not in html


def test_launcher_hides_apps_for_wrong_role(hub_app):
    add_app(hub_app, "adm", "เฉพาะแอดมิน", required_role="admin")
    html = login_as(hub_app, role="viewer").get("/").get_data(as_text=True)
    assert "เฉพาะแอดมิน" not in html


def test_disabled_app_404_on_go(hub_app):
    add_app(hub_app, "off", "x", enabled=0)
    assert login_as(hub_app).get("/go/off").status_code == 404
    assert login_as(hub_app, username="b2").get("/go/nope").status_code == 404


def test_go_redirects_with_verifiable_token(hub_app):
    r = login_as(hub_app, role="supervisor").get("/go/safety")
    assert r.status_code == 302
    u = urlparse(r.headers["Location"])
    assert f"{u.scheme}://{u.netloc}{u.path}" == "http://safety.test/sso"
    claims = verify_token(SECRET, parse_qs(u.query)["token"][0])
    assert claims["name"] == "สมชาย" and claims["roles"] == ["supervisor"] and claims["uid"] >= 1
    assert claims["aud"] == "safety"


def test_go_forbidden_for_wrong_role(hub_app):
    add_app(hub_app, "adm", "เฉพาะแอดมิน", required_role="admin")
    assert login_as(hub_app, role="viewer").get("/go/adm").status_code == 403


def test_disabled_user_session_refused_on_go(hub_app):
    c = login_as(hub_app)
    sql(hub_app, "UPDATE users SET active=0")
    r = c.get("/go/safety")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_seed_is_idempotent(hub_app):
    con = connect(hub_app.config["DATABASE"])
    seed_default_apps(con, "http://safety.test")
    seed_default_apps(con, "http://safety.test")
    assert con.execute("SELECT COUNT(*) FROM app_registry WHERE key='safety'").fetchone()[0] == 1
