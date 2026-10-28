#!/usr/bin/env python3
"""safety/run.py — เปิด safety ด้วย Waitress (พอร์ต SAFETY_PORT ค่าเริ่มต้น 5200); อ่านค่าจาก .env ที่รากโปรเจกต์"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared.envfile import load_env  # noqa: E402

load_env(os.path.join(ROOT, ".env"))

from safety.app import create_app  # noqa: E402

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("SAFETY_PORT", "5200"))
    app = create_app()
    if os.environ.get("SAFETY_DISABLE_BACKGROUND") != "1":      # ตัวส่งแจ้งเตือน + งานประจำวัน (ตั้ง =1 เพื่อปิด เช่นรันหลายโปรเซส)
        from safety.delivery import start_worker
        from safety.scheduler import start_scheduler
        start_worker(app)
        start_scheduler(app)
    print(f"safety พร้อมที่ http://0.0.0.0:{port}")
    serve(app, host="0.0.0.0", port=port)
