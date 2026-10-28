"""สร้างไฟล์ Zip สำหรับติดตั้ง (Windows): python tools/build_release.py → dist/AIAT-Safety-Hub-YYYYMMDD.zip
รวมเฉพาะโค้ดที่ใช้รัน + สคริปต์ .bat + เอกสาร (ไม่รวมเทสต์ ฐานข้อมูล .env หรือ .git)"""
import os
import sys
import zipfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "AIAT-Safety-Hub"
INCLUDE_DIRS = ("hub", "safety", "shared", "docs")
INCLUDE_TOOLS = ("make_env.py", "selfcheck.py", "backup.py", "ports.py")
INCLUDE_FILES = ("requirements.txt", ".env.example", "README.md")
SKIP_DIRS = {"__pycache__", "data", "superpowers", ".pytest_cache"}
TEXT_EXT = (".bat", ".txt")


def files():
    for d in INCLUDE_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, d)):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for n in sorted(names):
                if not n.endswith((".pyc", ".db", ".db-wal", ".db-shm", ".log")):
                    yield os.path.join(base, n)
    for n in INCLUDE_TOOLS:
        yield os.path.join(ROOT, "tools", n)
    for n in INCLUDE_FILES:
        yield os.path.join(ROOT, n)


def main() -> str:
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", f"{NAME}-{date.today():%Y%m%d}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files():
            z.write(path, f"{NAME}/{os.path.relpath(path, ROOT).replace(os.sep, '/')}")
        win = os.path.join(ROOT, "deploy", "windows")
        for n in sorted(os.listdir(win)):
            data = open(os.path.join(win, n), "rb").read()
            if n.endswith(TEXT_EXT):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")       # Windows ต้องการ CRLF
                if n.endswith(".bat"):
                    pass
            z.writestr(f"{NAME}/{n}", data)
        z.writestr(f"{NAME}/hub/data/.keep", b"")
        z.writestr(f"{NAME}/safety/data/.keep", b"")
    return out


if __name__ == "__main__":
    print(main())
