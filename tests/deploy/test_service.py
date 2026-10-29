"""tools/service.py: เปิด/ปิด/เปิดใหม่เบื้องหลังด้วยโปรเซสจริง (เซิร์ฟเวอร์จำลอง) — ปิดเฉพาะของที่ระบบเปิดเอง ไม่แตะโปรแกรมอื่น"""
import os
import socket
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import service  # noqa: E402

SERVER = "import socket,sys,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',int(sys.argv[1])));s.listen(5);print('up',flush=True);time.sleep(600)"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def svcs(tmp_path):
    out = []
    for name in ("Hub", "Safety"):
        port = free_port()
        out.append({"name": name, "port": port, "cmd": [sys.executable, "-c", SERVER, str(port)],
                    "log": str(tmp_path / name / "x.log"), "pid": str(tmp_path / name / "x.pid")})
    yield out
    service.stop(out)


def test_start_runs_in_background_writes_pid_and_log_and_is_idempotent(svcs, capsys):
    assert service.start(svcs, ready_seconds=10) == 0
    pids = [service.read_pid(s["pid"]) for s in svcs]
    assert all(p and service.pid_alive(p) for p in pids) and all(service.listening(s["port"]) for s in svcs)
    assert "up" in open(svcs[0]["log"]).read()                                           # stdout ไปไฟล์ log ไม่ใช่หน้าจอ
    assert service.start(svcs, ready_seconds=10) == 0                                    # เรียกซ้ำ = ข้าม ไม่เปิดซ้ำ
    assert [service.read_pid(s["pid"]) for s in svcs] == pids and "เปิดอยู่แล้ว" in capsys.readouterr().out


def test_stop_kills_only_our_processes_and_cleans_pid_files(svcs):
    service.start(svcs, ready_seconds=10)
    stranger = socket.socket(); stranger.bind(("127.0.0.1", 0)); stranger.listen(1)           # โปรแกรมอื่นบนเครื่อง (เช่น E-Checksheet)
    sport = stranger.getsockname()[1]
    pids = [service.read_pid(s["pid"]) for s in svcs]
    service.stop(svcs)
    for _ in range(40):
        if not any(service.pid_alive(p) for p in pids):
            break
        time.sleep(0.1)
    assert not any(service.listening(s["port"]) for s in svcs) and not any(os.path.exists(s["pid"]) for s in svcs)
    assert service.listening(sport)                                                      # ของคนอื่นไม่ถูกแตะ
    stranger.close()


def test_start_refuses_a_port_used_by_another_program_without_killing_it(svcs, capsys):
    other = socket.socket(); other.bind(("127.0.0.1", svcs[0]["port"])); other.listen(1)
    assert service.start(svcs, ready_seconds=10) == 1
    out = capsys.readouterr().out
    assert "มีโปรแกรมอื่นใช้อยู่" in out and service.listening(svcs[1]["port"])           # Hub ชน แต่ Safety ยังเปิดได้
    assert not os.path.exists(svcs[0]["pid"])
    other.close()


def test_start_reports_failure_when_server_crashes(tmp_path, capsys):
    bad = {"name": "Hub", "port": free_port(), "cmd": [sys.executable, "-c", "raise SystemExit('boom')"], "log": str(tmp_path / "l.log"), "pid": str(tmp_path / "p.pid")}
    assert service.start([bad], ready_seconds=5) == 1 and "เปิดไม่สำเร็จ" in capsys.readouterr().out
    service.stop([bad])


def test_restart_gives_new_pids_and_keeps_serving(svcs):
    service.start(svcs, ready_seconds=10)
    old = [service.read_pid(s["pid"]) for s in svcs]
    service.stop(svcs)
    time.sleep(0.3)
    assert service.start(svcs, ready_seconds=10) == 0
    new = [service.read_pid(s["pid"]) for s in svcs]
    assert new != old and all(service.listening(s["port"]) for s in svcs)


def test_sync_deps_installs_only_when_requirements_changed(tmp_path, monkeypatch):
    (tmp_path / ".venv").mkdir()
    (tmp_path / "requirements.txt").write_text("flask\n")
    calls = []
    monkeypatch.setattr(service, "deps_importable", lambda py: False)
    monkeypatch.setattr(service.subprocess, "run", lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0})())
    assert service.sync_deps(str(tmp_path), "py") and len(calls) == 1 and calls[0][:4] == ["py", "-m", "pip", "install"]
    assert service.sync_deps(str(tmp_path), "py") and len(calls) == 1                     # ไม่เปลี่ยน = ไม่ติดตั้งซ้ำ
    (tmp_path / "requirements.txt").write_text("flask\nnewlib\n")
    assert service.sync_deps(str(tmp_path), "py") and len(calls) == 2


def test_sync_deps_failure_returns_false_and_does_not_mark(tmp_path, monkeypatch):
    (tmp_path / ".venv").mkdir()
    (tmp_path / "requirements.txt").write_text("x\n")
    monkeypatch.setattr(service.subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 1})())
    assert service.sync_deps(str(tmp_path), "py") is False and not os.path.exists(service.deps_marker(str(tmp_path)))


def test_windows_scripts_exist_use_service_and_ship_in_the_release():
    win = os.path.join(ROOT, "deploy", "windows")
    restart = open(os.path.join(win, "RestartPortal.bat"), encoding="utf-8").read()
    assert "service.py restart" in restart and "install.bat" in restart.rsplit(":noinstall", 1)[1]
    assert "service.py start --open" in open(os.path.join(win, "start.bat"), encoding="utf-8").read()
    assert "start /min" not in open(os.path.join(win, "start.bat"), encoding="utf-8").read()
    assert "service.py stop" in open(os.path.join(win, "stop.bat"), encoding="utf-8").read()
    assert "boot" in open(os.path.join(win, "autostart.bat"), encoding="utf-8").read()
    assert "markdeps" in open(os.path.join(win, "install.bat"), encoding="utf-8").read()
    import zipfile, build_release
    z = zipfile.ZipFile(build_release.main())
    names = z.namelist()
    assert "AIAT-Safety-Hub/RestartPortal.bat" in names and "AIAT-Safety-Hub/tools/service.py" in names
    assert z.read("AIAT-Safety-Hub/RestartPortal.bat").count(b"\r\n") == z.read("AIAT-Safety-Hub/RestartPortal.bat").count(b"\n")


def test_services_read_ports_from_env_file(tmp_path):
    (tmp_path / ".env").write_text("HUB_PORT=5001\nSAFETY_PORT=5201\n")
    h, s = service.services(str(tmp_path), "py")
    assert (h["port"], s["port"]) == (5001, 5201) and h["cmd"][0] == "py" and h["pid"].endswith("hub.pid")


def test_upgrade_from_old_install_without_marker_needs_no_internet_when_libraries_present(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("flask\n")                        # ไม่มี .venv/marker เลย = ติดตั้งมาจากเวอร์ชันก่อน
    calls = []
    monkeypatch.setattr(service, "deps_importable", lambda py: True)
    monkeypatch.setattr(service.subprocess, "run", lambda cmd, **k: calls.append(cmd) or type("R", (), {"returncode": 0})())
    assert service.sync_deps(str(tmp_path), "py") and calls == [] and os.path.exists(service.deps_marker(str(tmp_path)))
