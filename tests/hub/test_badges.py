import io
import json
import socket
import urllib.error

from hub.views import launcher
from tests.hub.test_launcher import login_as

ROW = {"key": "safety", "url": "http://safety.test"}


class FakeResp(io.BytesIO):
    def __init__(self, body: dict, status: int = 200):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_badge_sums_counts_and_sends_token(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["url"], seen["tok"], seen["timeout"] = req.full_url, req.get_header("X-service-token"), timeout
        return FakeResp({"open_hazards": 3, "overdue": 2})

    monkeypatch.setattr(launcher, "urlopen", fake)
    assert launcher.fetch_badge(ROW, "svc-token") == 5
    assert seen == {"url": "http://safety.test/api/summary", "tok": "svc-token", "timeout": 2.0}


def test_fetch_badge_none_on_connection_error(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(launcher, "urlopen", boom)
    assert launcher.fetch_badge(ROW, "t") is None


def test_fetch_badge_none_on_timeout(monkeypatch):
    def slow(req, timeout):
        raise socket.timeout()
    monkeypatch.setattr(launcher, "urlopen", slow)
    assert launcher.fetch_badge(ROW, "t") is None


def test_fetch_badge_none_on_bad_status_or_body(monkeypatch):
    monkeypatch.setattr(launcher, "urlopen", lambda req, timeout: FakeResp({"open_hazards": 1, "overdue": 0}, 500))
    assert launcher.fetch_badge(ROW, "t") is None
    monkeypatch.setattr(launcher, "urlopen", lambda req, timeout: FakeResp({"x": "y"}))
    assert launcher.fetch_badge(ROW, "t") is None


def test_launcher_renders_when_badge_fails(hub_app, monkeypatch):
    def boom(req, timeout):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(launcher, "urlopen", boom)
    r = login_as(hub_app).get("/")
    assert r.status_code == 200 and "Safety" in r.get_data(as_text=True)
    assert 'class="badge"' not in r.get_data(as_text=True)


def test_launcher_shows_badge_when_positive(hub_app, monkeypatch):
    monkeypatch.setattr(launcher, "urlopen", lambda req, timeout: FakeResp({"open_hazards": 4, "overdue": 1}))
    html = login_as(hub_app).get("/").get_data(as_text=True)
    assert '<span class="badge">5<span class="sr-only"> รายการที่ต้องติดตาม</span></span>' in html
