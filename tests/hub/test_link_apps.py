from urllib.parse import urlparse

import pytest

from hub.db import connect
from hub.schema import ensure_schema, seed_link_apps
from tests.conftest import csrf
from tests.hub.test_launcher import login_as


def rows(app, q, args=()):
    con = connect(app.config["DATABASE"])
    try:
        return con.execute(q, args).fetchall()
    finally:
        con.close()


def add_link(app, key="utcmms", url="http://192.168.133.129:5000/", name="UT CMMS", mode="link", **kw):
    con = connect(app.config["DATABASE"])
    con.execute("INSERT INTO app_registry(key,name_th,url,icon,link_mode,required_role) VALUES(?,?,?,?,?,?)", (key, name, url, "app", mode, kw.get("role", "")))
    con.commit(); con.close()


def test_link_app_opens_url_as_is_without_sso_token(hub_app):
    add_link(hub_app, "echeck", "http://192.168.133.129:5000/echeck/dashboard", "E-Checksheet")
    r = login_as(hub_app).get("/go/echeck")
    assert r.status_code == 302 and r.headers["Location"] == "http://192.168.133.129:5000/echeck/dashboard"
    assert "token" not in r.headers["Location"] and "/sso" not in r.headers["Location"]


def test_sso_apps_still_get_a_token(hub_app):
    add_link(hub_app, "x", "http://x.test", "X", mode="sso")
    loc = login_as(hub_app).get("/go/x").headers["Location"]
    assert urlparse(loc).path == "/sso" and "token=" in loc


def test_launcher_shows_link_tiles_opening_in_new_tab(hub_app):
    add_link(hub_app, "utcmms"); add_link(hub_app, "echeck", "http://192.168.133.129:5000/echeck/dashboard", "E-Checksheet")
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert 'href="/go/utcmms"' in html and 'href="/go/echeck"' in html and "UT CMMS" in html and "E-Checksheet" in html
    assert 'target="_blank"' in html and 'rel="noopener noreferrer"' in html


def test_link_respects_required_role(hub_app):
    add_link(hub_app, "adm", "http://a.test/", "เฉพาะแอดมิน", role="admin")
    assert login_as(hub_app, "viewer1", "viewer").get("/go/adm").status_code == 403


def test_admin_can_add_link_app_and_bad_mode_rejected(hub_app):
    c = login_as(hub_app)
    form = {"_csrf": csrf(c), "action": "save", "key": "utcmms", "name_th": "UT CMMS", "url": "http://192.168.133.129:5000/", "icon": "app", "required_role": "", "sort": "20", "link_mode": "link"}
    assert c.post("/admin/apps", data=form).status_code == 302
    assert rows(hub_app, "SELECT link_mode FROM app_registry WHERE key='utcmms'")[0][0] == "link"
    c.post("/admin/apps", data={**form, "key": "bad1", "link_mode": "evil"})
    assert rows(hub_app, "SELECT 1 FROM app_registry WHERE key='bad1'") == []
    assert "เปิดลิงก์ตรง" in c.get("/admin/apps").get_data(as_text=True)


def test_seed_link_apps_from_env_idempotent_and_keeps_admin_edits(hub_app):
    con = connect(hub_app.config["DATABASE"])
    env = {"UTCMMS_URL": "http://192.168.133.129:5000/", "ECHECK_URL": "http://192.168.133.129:5000/echeck/dashboard"}
    seed_link_apps(con, env); seed_link_apps(con, env)
    got = {r["key"]: (r["url"], r["link_mode"]) for r in con.execute("SELECT * FROM app_registry")}
    assert got["utcmms"] == ("http://192.168.133.129:5000/", "link") and got["echeck"][1] == "link"
    con.execute("UPDATE app_registry SET url='http://edited.test/' WHERE key='utcmms'"); con.commit()
    seed_link_apps(con, env)
    assert con.execute("SELECT url FROM app_registry WHERE key='utcmms'").fetchone()[0] == "http://edited.test/"
    assert con.execute("SELECT COUNT(*) FROM app_registry WHERE key IN ('utcmms','echeck')").fetchone()[0] == 2
    seed_link_apps(con, {"UTCMMS_URL": "javascript:alert(1)", "ECHECK_URL": ""})                    # ค่าไม่ถูกต้อง/ว่าง ข้าม
    con.close()


def test_old_database_gets_link_mode_column(tmp_path):
    con = connect(str(tmp_path / "old.db"))
    con.executescript("CREATE TABLE app_registry(id INTEGER PRIMARY KEY, key TEXT UNIQUE, name_th TEXT, url TEXT, icon TEXT DEFAULT 'app', enabled INTEGER DEFAULT 1, required_role TEXT DEFAULT '', sort INTEGER DEFAULT 0);"
                      "CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, name_th TEXT DEFAULT '', password_hash TEXT, role TEXT DEFAULT 'viewer', active INTEGER DEFAULT 1);"
                      "INSERT INTO app_registry(key,name_th,url) VALUES('safety','S','http://s.test');")
    ensure_schema(con); ensure_schema(con)
    assert con.execute("SELECT link_mode FROM app_registry").fetchone()[0] == "sso"
