"""สำรองฐานข้อมูลทั้งสองด้วย sqlite backup API (ปลอดภัยขณะระบบทำงาน) และคัดลอกรูปภาพ — ใช้โดย backup.bat
ใช้: python tools/backup.py <โฟลเดอร์ปลายทาง>"""
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from shared.envfile import load_env  # noqa: E402

load_env(os.path.join(ROOT, ".env"))


def main(dest: str) -> int:
    os.makedirs(dest, exist_ok=True)
    jobs = {"hub.db": os.environ.get("HUB_DATABASE", os.path.join(ROOT, "hub", "data", "hub.db")),
            "safety.db": os.environ.get("SAFETY_DATABASE", os.path.join(ROOT, "safety", "data", "safety.db"))}
    for name, path in jobs.items():
        if not os.path.exists(path):
            print("ข้าม (ยังไม่มีไฟล์):", path)
            continue
        src, dst = sqlite3.connect(path), sqlite3.connect(os.path.join(dest, name))
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        print("สำรอง", name)
    uploads = os.environ.get("SAFETY_UPLOAD_ROOT", os.path.join(ROOT, "safety", "data", "uploads"))
    if os.path.isdir(uploads):
        shutil.copytree(uploads, os.path.join(dest, "uploads"), dirs_exist_ok=True)
        print("สำรองรูปภาพ")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else 2)
