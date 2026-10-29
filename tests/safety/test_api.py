import pytest

from safety.app import create_app


def test_summary_requires_token(safety_app):
    c = safety_app.test_client()
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/summary", headers={"X-Service-Token": "nope"}).status_code == 401


def test_summary_ok_with_token(safety_app):
    r = safety_app.test_client().get("/api/summary", headers={"X-Service-Token": "svc-token"})
    assert r.status_code == 200 and r.get_json() == {"open_hazards": 0, "overdue": 0}


def test_summary_denied_when_service_token_unset(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "s.db"), "SECRET_KEY": "k",
                      "SSO_SECRET": "t" * 40, "SERVICE_TOKEN": "", "HUB_URL": "http://hub.test"})
    c = app.test_client()
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/summary", headers={"X-Service-Token": ""}).status_code == 401
