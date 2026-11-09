#!/usr/bin/env python3
"""hub/run.py — เปิด hub ด้วย Waitress (พอร์ต HUB_PORT ค่าเริ่มต้น 5000); อ่านค่าจาก .env ที่รากโปรเจกต์"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shared.envfile import load_env  # noqa: E402

load_env(os.path.join(ROOT, ".env"))

from hub.app import create_app  # noqa: E402

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("HUB_PORT", "5000"))
    print(f"hub พร้อมที่ http://0.0.0.0:{port}")
    serve(create_app(), host="0.0.0.0", port=port)
