"""ตัวช่วยเรื่องพอร์ตสำหรับ .bat (ทำเป็น Python เพื่อให้ทดสอบได้ — batch ล้วนเปราะ)
  ports.py suggest <พอร์ตเริ่มต้น> [พอร์ตที่ต้องเลี่ยง]   พิมพ์พอร์ตแรกที่ว่าง (ตั้งแต่พอร์ตเริ่มต้นขึ้นไป)
  ports.py show                                        พิมพ์ "<HUB_PORT> <SAFETY_PORT>" จาก .env (ไม่มี = 5000 5200)
  ports.py inuse                                       พิมพ์พอร์ตของระบบนี้ที่มีโปรแกรมอื่นใช้อยู่ (exit 1 ถ้ามี)"""
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {"HUB_PORT": 5000, "SAFETY_PORT": 5200}
LOW, HIGH = 1024, 65535


def is_free(port: int) -> bool:
    for host in ("0.0.0.0", "127.0.0.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def suggest(start: int, avoid=()) -> int:
    for port in range(max(start, LOW), HIGH + 1):
        if port not in avoid and is_free(port):
            return port
    raise SystemExit("ไม่พบพอร์ตว่าง")


def read_ports(env_path: str | None = None) -> dict:
    ports = dict(DEFAULTS)
    try:
        lines = open(env_path or os.path.join(ROOT, ".env"), encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return ports
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key.strip() in ports and value.strip().strip('"').strip("'").isdigit():
            ports[key.strip()] = int(value.strip().strip('"').strip("'"))
    return ports


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "suggest":
        start = int(argv[2])
        print(suggest(start, {int(x) for x in argv[3:]}))
        return 0
    if cmd == "show":
        p = read_ports()
        print(p["HUB_PORT"], p["SAFETY_PORT"])
        return 0
    if cmd == "inuse":
        p = read_ports()
        busy = [f"{name}={port}" for name, port in p.items() if not is_free(port)]
        if busy:
            print(" ".join(busy))
        return 1 if busy else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
