"""ควบคุม Hub และ Safety เป็นโปรเซสเบื้องหลัง (ไม่มีหน้าต่างค้างบนแถบงาน) — ใช้โดย start.bat / stop.bat / RestartPortal.bat
  service.py start [--open]   เปิดทั้งสองโปรแกรมเบื้องหลัง (ถ้าเปิดอยู่แล้วข้าม) แล้วรอจนพอร์ตพร้อม; --open เปิดเบราว์เซอร์ที่หน้า Hub
  service.py stop             ปิดเฉพาะโปรเซสที่ระบบนี้เปิดไว้ (จาก pid ไฟล์) ไม่แตะโปรแกรมอื่นบนเครื่อง เช่น E-Checksheet/UTCMMS
  service.py restart [--open] ปิดแล้วเปิดใหม่ + อัปเดตไลบรารีถ้า requirements.txt เปลี่ยน (ไม่ต้องรัน install.bat ซ้ำ)
  service.py status           แสดงสถานะ
  service.py markdeps         บันทึกว่าไลบรารีตรงกับ requirements.txt ปัจจุบัน (install.bat เรียกหลังติดตั้ง)"""
from __future__ import annotations

import hashlib
import os
import signal
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
WIN = os.name == "nt"
READY_SECONDS = 20


def services(root: str = ROOT, python: str | None = None) -> list[dict]:
    from tools.ports import read_ports
    ports = read_ports(os.path.join(root, ".env"))
    py = python or sys.executable
    return [{"name": "Hub", "port": ports["HUB_PORT"], "cmd": [py, os.path.join(root, "hub", "run.py")],
             "log": os.path.join(root, "hub", "data", "hub.log"), "pid": os.path.join(root, "hub", "data", "hub.pid")},
            {"name": "Safety", "port": ports["SAFETY_PORT"], "cmd": [py, os.path.join(root, "safety", "run.py")],
             "log": os.path.join(root, "safety", "data", "safety.log"), "pid": os.path.join(root, "safety", "data", "safety.pid")}]


def listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(path: str) -> int:
    try:
        return int(open(path).read().strip())
    except (OSError, ValueError):
        return 0


def kill_tree(pid: int) -> None:
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    for _ in range(30):                                   # รอให้ปิดเอง ถ้าไม่ปิดค่อยบังคับ
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(os.getpgid(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
    except (ProcessLookupError, PermissionError):
        pass


def spawn(svc: dict, env: dict | None = None) -> int:
    os.makedirs(os.path.dirname(svc["log"]), exist_ok=True)
    log = open(svc["log"], "ab")
    kw: dict = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "cwd": ROOT, "env": {**os.environ, "PYTHONUTF8": "1", **(env or {})}}
    if WIN:
        kw["creationflags"] = 0x08000000 | 0x00000200 | 0x00000008              # NO_WINDOW | NEW_PROCESS_GROUP | DETACHED
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(svc["cmd"], **kw)
    log.close()
    with open(svc["pid"], "w") as fh:
        fh.write(str(proc.pid))
    return proc.pid


def start(svcs: list[dict], ready_seconds: float = READY_SECONDS, env: dict | None = None) -> int:
    code = 0
    started = []
    for s in svcs:
        pid = read_pid(s["pid"])
        if pid and pid_alive(pid) and listening(s["port"]):
            print(f"[=] {s['name']} เปิดอยู่แล้ว (พอร์ต {s['port']})")
            continue
        if listening(s["port"]):
            print(f"[X] {s['name']}: พอร์ต {s['port']} มีโปรแกรมอื่นใช้อยู่ — ถ้าเป็นระบบนี้ที่เปิดแบบเก่าค้างอยู่ให้รัน stop.bat ก่อน "
                  f"ถ้าเป็นระบบอื่น (E-Checksheet/UTCMMS) ให้แก้พอร์ตใน .env")
            code = 1
            continue
        os.makedirs(os.path.dirname(s["log"]), exist_ok=True)
        spawn(s, env)
        started.append(s)
    deadline = time.time() + ready_seconds
    for s in started:
        while time.time() < deadline and not listening(s["port"]):
            if not pid_alive(read_pid(s["pid"])):
                break
            time.sleep(0.25)
        if listening(s["port"]):
            print(f"[OK] {s['name']} พร้อมที่พอร์ต {s['port']}")
        else:
            print(f"[X] {s['name']} เปิดไม่สำเร็จ — ดูสาเหตุในไฟล์ {s['log']}")
            code = 1
    return code


def stop(svcs: list[dict]) -> None:
    for s in svcs:
        pid = read_pid(s["pid"])
        if pid and pid_alive(pid):
            kill_tree(pid)
            print(f"[OK] ปิด {s['name']} แล้ว")
        else:
            print(f"[=] {s['name']} ไม่ได้เปิดอยู่ (ตามที่ระบบนี้บันทึกไว้)")
        try:
            os.remove(s["pid"])
        except OSError:
            pass


def status(svcs: list[dict]) -> int:
    ok = 0
    for s in svcs:
        up = listening(s["port"])
        pid = read_pid(s["pid"])
        print(f"{s['name']}: {'เปิดอยู่' if up else 'ปิดอยู่'} · พอร์ต {s['port']}" + (f" · pid {pid}" if pid and pid_alive(pid) else ""))
        ok += up
    return 0 if ok == len(svcs) else 1


def deps_hash(root: str = ROOT) -> str:
    return hashlib.sha256(open(os.path.join(root, "requirements.txt"), "rb").read()).hexdigest()


def deps_marker(root: str = ROOT) -> str:
    return os.path.join(root, ".venv", "requirements.sha256")


def mark_deps(root: str = ROOT) -> None:
    os.makedirs(os.path.dirname(deps_marker(root)), exist_ok=True)
    with open(deps_marker(root), "w") as fh:
        fh.write(deps_hash(root))


def deps_importable(python: str) -> bool:
    code = "import flask, waitress, PIL, openpyxl, reportlab"
    return subprocess.run([python, "-c", code], capture_output=True).returncode == 0


def sync_deps(root: str = ROOT, python: str | None = None) -> bool:
    """ติดตั้งไลบรารีใหม่เฉพาะเมื่อ requirements.txt เปลี่ยนจากที่เคยติดตั้ง; คืน False ถ้าติดตั้งไม่สำเร็จ"""
    if not os.path.exists(os.path.join(root, "requirements.txt")):
        return True
    want = deps_hash(root)
    try:
        have = open(deps_marker(root)).read().strip()
    except FileNotFoundError:                          # ติดตั้งมาจากเวอร์ชันเก่าที่ยังไม่มีเครื่องหมาย: ถ้าไลบรารีหลักครบอยู่แล้วก็แค่บันทึก ไม่ต้องต่ออินเทอร์เน็ต
        if deps_importable(python or sys.executable):
            mark_deps(root)
            return True
        have = ""
    if want == have:
        return True
    print("พบไลบรารีใหม่ในเวอร์ชันนี้ กำลังติดตั้ง (ต้องต่ออินเทอร์เน็ต) ...")
    r = subprocess.run([python or sys.executable, "-m", "pip", "install", "-r", os.path.join(root, "requirements.txt")])
    if r.returncode != 0:
        print("[X] ติดตั้งไลบรารีไม่สำเร็จ — ตรวจอินเทอร์เน็ตแล้วรัน RestartPortal.bat ใหม่ (ระบบเดิมยังเปิดอยู่)")
        return False
    mark_deps(root)
    return True


def open_browser(port: int) -> None:
    import webbrowser
    webbrowser.open(f"http://localhost:{port}")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    flags = set(argv[2:])
    svcs = services()
    if cmd == "start":
        code = start(svcs)
        if code == 0 and "--open" in flags:
            open_browser(svcs[0]["port"])
        return code
    if cmd == "stop":
        stop(svcs)
        return 0
    if cmd == "restart":
        if not sync_deps():
            return 1                                    # ติดตั้งไลบรารีไม่ได้ → ไม่ปิดระบบเดิม
        stop(svcs)
        time.sleep(1.0)
        code = start(svcs)
        if code == 0 and "--open" in flags:
            open_browser(svcs[0]["port"])
        return code
    if cmd == "status":
        return status(svcs)
    if cmd == "markdeps":
        mark_deps()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
