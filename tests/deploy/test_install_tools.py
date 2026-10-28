import os
import socket
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ports  # noqa: E402


def run(*args, cwd=ROOT, env=None):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=cwd, env=env)


def hold(port=0):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0); s.bind(("0.0.0.0", port)); s.listen(1)
    return s, s.getsockname()[1]


def test_suggest_skips_busy_and_avoided_ports():
    s, busy = hold()
    try:
        assert ports.suggest(busy) != busy
        free = ports.suggest(busy)
        assert ports.suggest(busy, {free}) != free
    finally:
        s.close()
    assert ports.is_free(busy)


def test_read_ports_defaults_and_env_file(tmp_path):
    assert ports.read_ports(str(tmp_path / "none.env")) == {"HUB_PORT": 5000, "SAFETY_PORT": 5200}
    f = tmp_path / ".env"
    f.write_text("HUB_PORT=5001\n# SAFETY_PORT=1\nSAFETY_PORT = 5201\nHUB_URL=x\n", encoding="utf-8")
    assert ports.read_ports(str(f)) == {"HUB_PORT": 5001, "SAFETY_PORT": 5201}
    f.write_text("HUB_PORT=abc\n", encoding="utf-8")
    assert ports.read_ports(str(f)) == {"HUB_PORT": 5000, "SAFETY_PORT": 5200}


def make_env(tmp_path, *args):
    """รัน make_env.py ในสำเนาที่มี .env แยก (ไม่แตะ .env จริงของ repo)"""
    tools = tmp_path / "tools"; tools.mkdir(exist_ok=True)
    (tools / "make_env.py").write_text(open(os.path.join(ROOT, "tools", "make_env.py"), encoding="utf-8").read(), encoding="utf-8")
    r = run(str(tools / "make_env.py"), *args, cwd=str(tmp_path))
    return r, tmp_path / ".env"


def test_make_env_writes_ports_urls_and_random_secrets(tmp_path):
    r, env = make_env(tmp_path, "192.168.1.10", "5001", "5201")
    assert r.returncode == 0, r.stdout + r.stderr
    text = env.read_text(encoding="utf-8")
    for line in ("HUB_PORT=5001", "SAFETY_PORT=5201", "HUB_URL=http://192.168.1.10:5001", "SAFETY_URL=http://192.168.1.10:5201"):
        assert line in text.splitlines()
    vals = dict(l.split("=", 1) for l in text.splitlines() if "=" in l and not l.startswith("#"))
    assert len(vals["SSO_SECRET"]) >= 32 and vals["SSO_SECRET"] != vals["SERVICE_TOKEN"] and vals["HUB_SECRET_KEY"] != vals["SAFETY_SECRET_KEY"]
    assert make_env(tmp_path, "other", "6000", "6001")[0].stdout.startswith(".env มีอยู่แล้ว")           # ไม่ทับของเดิม
    assert "5001" in env.read_text(encoding="utf-8")


@pytest.mark.parametrize("args", [("localhost", "80", "5200"), ("localhost", "5000", "5000"), ("localhost", "abc", "5200"), ("bad host!", "5000", "5200"), ("localhost", "70000", "5200")])
def test_make_env_rejects_bad_input_and_writes_nothing(tmp_path, args):
    r, env = make_env(tmp_path, *args)
    assert r.returncode == 2 and not env.exists()


def test_make_env_defaults_when_blank(tmp_path):
    r, env = make_env(tmp_path, "", "", "")
    assert r.returncode == 0 and "HUB_URL=http://localhost:5000" in env.read_text(encoding="utf-8")


def test_bat_scripts_have_no_multiline_blocks_and_use_ports_from_env():
    bat_dir = os.path.join(ROOT, "deploy", "windows")
    for name in os.listdir(bat_dir):
        if not name.endswith(".bat"):
            continue
        text = open(os.path.join(bat_dir, name), encoding="utf-8").read()
        for n, line in enumerate(text.splitlines(), 1):
            assert not line.rstrip().endswith("("), f"{name}:{n} บล็อกหลายบรรทัดใน batch เปราะกับวงเล็บในข้อความ"
    start = open(os.path.join(bat_dir, "start.bat"), encoding="utf-8").read()
    auto = open(os.path.join(bat_dir, "autostart.bat"), encoding="utf-8").read()
    assert "localhost:%HUB_PORT%" in start and "ports.py show" in start and "ports.py inuse" in start
    assert "localport=%HUB_PORT%,%SAFETY_PORT%" in auto and "localhost:5000" not in start
    assert "ports.py suggest" in open(os.path.join(bat_dir, "install.bat"), encoding="utf-8").read()


def test_make_env_writes_legacy_link_urls_and_validates(tmp_path):
    r, env = make_env(tmp_path, "localhost", "5001", "5201", "http://192.168.133.129:5000/", "http://192.168.133.129:5000/echeck/dashboard")
    text = env.read_text(encoding="utf-8").splitlines()
    assert r.returncode == 0 and "UTCMMS_URL=http://192.168.133.129:5000/" in text and "ECHECK_URL=http://192.168.133.129:5000/echeck/dashboard" in text
    for bad in ("javascript:alert(1)", "http://a b/", 'http://x"y/'):
        d = tmp_path / "b"; d.mkdir(exist_ok=True)
        r2, env2 = make_env(d, "localhost", "5001", "5201", bad, "")
        assert r2.returncode == 2 and not env2.exists(), bad


def test_apply_timezone_works_without_time_tzset_like_windows(monkeypatch):
    import time
    from shared import timezone
    monkeypatch.delattr(time, "tzset", raising=False)
    monkeypatch.setenv("SAFETY_TIMEZONE", "Asia/Bangkok")
    assert timezone.apply_timezone() == "Asia/Bangkok"


def test_no_posix_only_calls_in_app_code():
    import re
    bad = re.compile(r"\b(os\.fork|os\.getuid|os\.geteuid|fcntl|termios|signal\.SIGKILL|os\.O_NOFOLLOW)\b")
    for base in ("hub", "safety", "shared", "tools"):
        for dirpath, _, names in os.walk(os.path.join(ROOT, base)):
            for n in names:
                if n.endswith(".py"):
                    assert not bad.search(open(os.path.join(dirpath, n), encoding="utf-8").read()), os.path.join(dirpath, n)


def test_bat_scripts_that_run_python_force_utf8():
    for n in ("install.bat", "start.bat", "backup.bat"):
        assert 'set "PYTHONUTF8=1"' in open(os.path.join(ROOT, "deploy", "windows", n), encoding="utf-8").read(), n
