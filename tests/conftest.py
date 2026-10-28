import pytest

SECRET = "t" * 40


@pytest.fixture
def hub_app(tmp_path):
    from hub.app import create_app
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "hub.db"), "SECRET_KEY": "k",
                       "SSO_SECRET": SECRET, "SERVICE_TOKEN": "svc-token",
                       "SAFETY_URL": "http://safety.test"})


@pytest.fixture
def safety_app(tmp_path):
    from safety.app import create_app
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "safety.db"), "SECRET_KEY": "k",
                       "SSO_SECRET": SECRET, "SERVICE_TOKEN": "svc-token",
                       "HUB_URL": "http://hub.test"})


def csrf(client) -> str:
    with client.session_transaction() as s:
        s["_csrf"] = "tok"
    return "tok"
