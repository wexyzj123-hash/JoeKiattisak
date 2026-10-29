import io
import json
import logging
import time
import urllib.error

import pytest

from safety import hubcheck
from safety.app import create_app
from tests.conftest import SECRET
from tests.safety.helpers import login, make_user


class Resp(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode()); self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "s.db"), "SECRET_KEY": "k", "SSO_SECRET": SECRET,
                       "SERVICE_TOKEN": "svc-token", "HUB_URL": "http://hub.test", "HUB_STATUS_CHECK": True,
                       "UPLOAD_ROOT": str(tmp_path / "up")})


def hub_says(monkeypatch, state):
    """state: dict hub_uid -> active bool / 'error' / 'slow'; คืนรายการการเรียกเพื่อนับ"""
    calls = []

    def fake(req, timeout):
        calls.append((req.full_url, req.get_header("X-service-token"), timeout))
        uid = int(req.full_url.rsplit("/", 1)[1])
        s = state.get(uid, True)
        if s == "error":
            raise urllib.error.URLError("hub down")
        if s == "404":
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return Resp({"active": s, "role": "viewer"})
    monkeypatch.setattr(hubcheck, "_urlopen", fake)
    return calls


def test_active_hub_user_passes_and_call_is_authenticated(app, monkeypatch):
    calls = hub_says(monkeypatch, {})
    c = login(app, "reporter", hub_uid=11)
    assert c.get("/hazards").status_code == 200
    assert calls[0][0] == "http://hub.test/api/user-status/11" and calls[0][1] == "svc-token" and calls[0][2] <= 2


def test_disabled_hub_user_signed_out_within_cache_ttl(app, monkeypatch):
    state = {11: True}
    calls = hub_says(monkeypatch, state)
    clock = {"t": 1000.0}
    monkeypatch.setattr(hubcheck, "_now", lambda: clock["t"])
    c = login(app, "reporter", hub_uid=11)
    assert c.get("/hazards").status_code == 200
    state[11] = False                                                                        # Hub ปิดผู้ใช้
    clock["t"] += 30
    assert c.get("/hazards").status_code == 200                                              # ยังอยู่ใน cache 60 วินาที
    clock["t"] += 31
    r = c.get("/hazards")
    assert r.status_code == 302 and r.headers["Location"] == "http://hub.test"
    with c.session_transaction() as s:
        assert "suid" not in s
    assert c.get("/hazards").status_code == 302                                              # ออกจากระบบจริง


def test_unknown_hub_user_404_is_signed_out(app, monkeypatch):
    hub_says(monkeypatch, {12: "404"})
    assert login(app, "reporter", hub_uid=12).get("/hazards").status_code == 302


def test_cache_avoids_a_hub_call_per_request(app, monkeypatch):
    calls = hub_says(monkeypatch, {})
    c = login(app, "reporter", hub_uid=13)
    for _ in range(10):
        c.get("/hazards")
    assert len(calls) == 1


def test_hub_down_fails_open_and_logs_once(app, monkeypatch, caplog):
    calls = hub_says(monkeypatch, {14: "error"})
    clock = {"t": 5000.0}
    monkeypatch.setattr(hubcheck, "_now", lambda: clock["t"])
    c = login(app, "reporter", hub_uid=14)
    with caplog.at_level(logging.WARNING, logger="safety.hubcheck"):
        for _ in range(3):
            assert c.get("/hazards").status_code == 200
            clock["t"] += 20                                                                 # เกินช่วงจำ "ล้มเหลว" 15 วินาที → ถามใหม่
    assert len(calls) >= 2 and caplog.text.count("Hub unreachable") == 1


def test_failed_lookups_are_cached_briefly_so_slow_hub_does_not_stall_every_request(app, monkeypatch):
    calls = hub_says(monkeypatch, {15: "error"})
    c = login(app, "reporter", hub_uid=15)
    for _ in range(8):
        assert c.get("/hazards").status_code == 200
    assert len(calls) == 1


def test_recovery_after_outage_logs_again_on_next_outage(app, monkeypatch, caplog):
    state = {16: "error"}
    hub_says(monkeypatch, state)
    clock = {"t": 9000.0}
    monkeypatch.setattr(hubcheck, "_now", lambda: clock["t"])
    c = login(app, "reporter", hub_uid=16)
    with caplog.at_level(logging.WARNING, logger="safety.hubcheck"):
        c.get("/hazards"); clock["t"] += 20
        state[16] = True; c.get("/hazards"); clock["t"] += 100                                # Hub กลับมา
        state[16] = "error"; c.get("/hazards")
    assert caplog.text.count("Hub unreachable") == 2


def test_real_timeout_does_not_hang_the_request(app):
    import socket
    import threading
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)                        # รับการเชื่อมต่อแต่ไม่ตอบ
    app.config["HUB_URL"] = f"http://127.0.0.1:{srv.getsockname()[1]}"
    c = login(app, "reporter", hub_uid=17)
    t0 = time.time()
    assert c.get("/hazards").status_code == 200
    assert time.time() - t0 < 4
    srv.close()


def test_sso_api_static_and_signout_paths_not_rechecked(app, monkeypatch):
    calls = hub_says(monkeypatch, {18: False})
    c = login(app, "reporter", hub_uid=18)
    c.get("/api/summary", headers={"X-Service-Token": "svc-token"})
    c.get("/static/theme.css")
    c.get("/signout")
    c.get("/sso?token=bad")
    assert calls == []


def test_check_disabled_by_config(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "s.db"), "SECRET_KEY": "k", "SSO_SECRET": SECRET,
                      "SERVICE_TOKEN": "x", "HUB_URL": "http://hub.test", "HUB_STATUS_CHECK": False})
    calls = hub_says(monkeypatch, {19: False})
    assert login(app, "reporter", hub_uid=19).get("/hazards").status_code == 200 and calls == []


def test_missing_service_token_means_no_check(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "s.db"), "SECRET_KEY": "k", "SSO_SECRET": SECRET,
                      "SERVICE_TOKEN": "", "HUB_URL": "http://hub.test", "HUB_STATUS_CHECK": True})
    calls = hub_says(monkeypatch, {20: False})
    assert login(app, "reporter", hub_uid=20).get("/hazards").status_code == 200 and calls == []


def test_per_app_cache_isolation(tmp_path, monkeypatch):
    mk = lambda n: create_app({"TESTING": True, "DATABASE": str(tmp_path / f"{n}.db"), "SECRET_KEY": "k", "SSO_SECRET": SECRET,
                               "SERVICE_TOKEN": "x", "HUB_URL": "http://hub.test", "HUB_STATUS_CHECK": True, "UPLOAD_ROOT": str(tmp_path / n)})
    a, b = mk("a"), mk("b")
    calls = hub_says(monkeypatch, {})
    login(a, "reporter", hub_uid=30).get("/hazards"); login(b, "reporter", hub_uid=30).get("/hazards")
    assert len(calls) == 2


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_service_token_is_logged_as_an_error_every_refresh_and_fails_open(app, monkeypatch, caplog, code):
    calls = []

    def reject(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
    monkeypatch.setattr(hubcheck, "_urlopen", reject)
    clock = {"t": 100.0}
    monkeypatch.setattr(hubcheck, "_now", lambda: clock["t"])
    c = login(app, "reporter", hub_uid=21)
    with caplog.at_level(logging.WARNING, logger="safety.hubcheck"):
        for _ in range(3):
            assert c.get("/hazards").status_code == 200                                       # fail open: ใช้งานต่อได้
            clock["t"] += 20
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(calls) == 3 and len(errors) == 3
    assert all("SERVICE_TOKEN" in r.getMessage() and "svc-token" not in r.getMessage() for r in errors)
