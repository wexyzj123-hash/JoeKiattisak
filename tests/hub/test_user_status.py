from hub.db import connect
from hub.security import hash_password
from tests.hub.test_auth import add_user

H = {"X-Service-Token": "svc-token"}


def uid_of(app, username):
    con = connect(app.config["DATABASE"])
    try:
        return con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()[0]
    finally:
        con.close()


def test_status_api_requires_token_and_reports_active_flag_and_role(hub_app):
    add_user(hub_app, "boss", role="supervisor")
    add_user(hub_app, "gone", role="viewer", active=0)
    c = hub_app.test_client()
    uid, gone = uid_of(hub_app, "boss"), uid_of(hub_app, "gone")
    for headers in ({}, {"X-Service-Token": "wrong"}, {"X-Service-Token": ""}):
        assert c.get(f"/api/user-status/{uid}", headers=headers).status_code == 401
    r = c.get(f"/api/user-status/{uid}", headers=H)
    assert r.status_code == 200 and r.get_json() == {"active": True, "role": "supervisor"}
    assert c.get(f"/api/user-status/{gone}", headers=H).get_json() == {"active": False, "role": "viewer"}
    assert r.headers["Cache-Control"] == "no-store"


def test_status_api_unknown_user_404_and_no_session_needed(hub_app):
    c = hub_app.test_client()
    assert c.get("/api/user-status/99999", headers=H).status_code == 404
    assert c.get("/api/user-status/abc", headers=H).status_code == 404


def test_status_api_denied_when_service_token_unset(tmp_path):
    from hub.app import create_app
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "h.db"), "SECRET_KEY": "k", "SSO_SECRET": "t" * 40,
                      "SERVICE_TOKEN": "", "SAFETY_URL": "http://s"})
    add_user(app, "boss")
    c = app.test_client()
    assert c.get(f"/api/user-status/{uid_of(app, 'boss')}").status_code == 401
    assert c.get(f"/api/user-status/{uid_of(app, 'boss')}", headers={"X-Service-Token": ""}).status_code == 401
