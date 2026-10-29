import pytest

from hub.app import create_app
from hub.db import connect
from hub.security import hash_password
from tests.conftest import csrf


def add_user(app, username="boss", password="Passw0rd!", role="admin", active=1):
    con = connect(app.config["DATABASE"])
    con.execute("INSERT INTO users(username,name_th,password_hash,role,active) VALUES(?,?,?,?,?)",
                (username, "สมชาย", hash_password(password), role, active))
    con.commit()
    con.close()


def login(client, username="boss", password="Passw0rd!", next_url=None):
    url = "/login" + (f"?next={next_url}" if next_url else "")
    return client.post(url, data={"username": username, "password": password, "_csrf": csrf(client)})


def test_setup_creates_first_admin_then_404(hub_app):
    c = hub_app.test_client()
    assert c.get("/setup").status_code == 200
    r = c.post("/setup", data={"username": "root", "name_th": "ผู้ดูแล", "password": "Passw0rd!", "_csrf": csrf(c)})
    assert r.status_code == 302
    con = connect(hub_app.config["DATABASE"])
    row = con.execute("SELECT role, active FROM users WHERE username='root'").fetchone()
    assert (row["role"], row["active"]) == ("admin", 1)
    assert c.get("/setup").status_code == 404


def test_login_ok_and_bad_password(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    assert login(c, password="wrong").status_code == 200
    with c.session_transaction() as s:
        assert "uid" not in s
    r = login(c)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    with c.session_transaction() as s:
        assert "uid" in s


@pytest.mark.parametrize("nxt", ["https://evil.example", "//evil.example", "/\\evil.example"])
def test_login_blocks_open_redirect(hub_app, nxt):
    add_user(hub_app)
    r = login(hub_app.test_client(), next_url=nxt)
    assert r.status_code == 302
    assert r.headers["Location"] in ("/", "http://localhost/")


def test_login_follows_safe_relative_next(hub_app):
    add_user(hub_app)
    r = login(hub_app.test_client(), next_url="/go/safety")
    assert r.headers["Location"].endswith("/go/safety")


def test_post_without_csrf_is_400(hub_app):
    add_user(hub_app)
    r = hub_app.test_client().post("/login", data={"username": "boss", "password": "Passw0rd!"})
    assert r.status_code == 400


def test_logout_clears_session(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    login(c)
    r = c.post("/logout", data={"_csrf": csrf(c)})
    assert r.status_code == 200 and "no-store" in r.headers["Cache-Control"]
    with c.session_transaction() as s:
        assert "uid" not in s


@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_create_app_rejects_short_secret(tmp_path, secret):
    with pytest.raises(RuntimeError):
        create_app({"DATABASE": str(tmp_path / "h.db"), "SECRET_KEY": "k", "SSO_SECRET": secret,
                    "SERVICE_TOKEN": "s", "SAFETY_URL": "http://x"})


def test_disabled_user_cannot_login(hub_app):
    add_user(hub_app, active=0)
    c = hub_app.test_client()
    assert login(c).status_code == 200
    with c.session_transaction() as s:
        assert "uid" not in s


def test_get_logout_clears_session_for_safety_redirect(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    login(c)
    r = c.get("/logout")
    assert r.status_code == 200 and 'data-signout="http://safety.test/signout?quiet=1"' in r.get_data(as_text=True)
    with c.session_transaction() as s:
        assert "uid" not in s


@pytest.mark.parametrize("nxt", ["/%09/evil.example", "/%0a/x", "/%0d/x", "/%5C/evil.example", "/%2F/evil.example",
                                 "/ x", "/\x00x"])
def test_login_rejects_control_chars_and_encoded_slashes_in_next(hub_app, nxt):
    add_user(hub_app)
    r = login(hub_app.test_client(), next_url=nxt)
    assert r.status_code == 302
    assert r.headers["Location"] in ("/", "http://localhost/")


def test_cookie_names_are_app_specific(hub_app, safety_app):
    assert hub_app.config["SESSION_COOKIE_NAME"] == "aiat_hub_session"
    assert safety_app.config["SESSION_COOKIE_NAME"] == "aiat_safety_session"


@pytest.mark.parametrize("field", ["SSO_SECRET", "SERVICE_TOKEN"])
def test_create_app_rejects_shipped_placeholder_secrets(tmp_path, field):
    cfg = {"DATABASE": str(tmp_path / "h.db"), "SECRET_KEY": "k", "SSO_SECRET": "t" * 40,
           "SERVICE_TOKEN": "s", "SAFETY_URL": "http://x", field: "change-me-to-a-random-string-of-32+chars"}
    with pytest.raises(RuntimeError):
        create_app(cfg)


def test_hub_logout_also_signs_out_of_safety(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    login(c)
    r = c.post("/logout", data={"_csrf": csrf(c)})
    html = r.get_data(as_text=True)
    assert 'data-signout="http://safety.test/signout?quiet=1"' in html and "/static/hub/logout.js" in html and 'href="/"' in html


def test_logout_page_swaps_loopback_safety_url_to_the_host_the_user_opened(hub_app):
    add_user(hub_app)
    con = connect(hub_app.config["DATABASE"])
    con.execute("UPDATE app_registry SET url='http://localhost:5200' WHERE key='safety'"); con.commit()
    c = hub_app.test_client()
    login(c)
    html = c.get("/logout", headers={"Host": "192.168.1.5:5001"}).get_data(as_text=True)
    assert 'data-signout="http://192.168.1.5:5200/signout?quiet=1"' in html                 # ไม่ส่งผู้ใช้ไป localhost ของเครื่องตัวเอง


def test_logout_never_strands_the_user_when_safety_is_down(hub_app):
    add_user(hub_app)
    c = hub_app.test_client()
    login(c)
    js = c.get("/static/hub/logout.js")
    assert js.status_code == 200 and "location.replace('/')" in js.get_data(as_text=True) and "setTimeout(go" in js.get_data(as_text=True)
