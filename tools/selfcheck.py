"""ตรวจว่าแอปทั้งสองโหลดได้จริงด้วยค่าใน .env (ใช้ฐานข้อมูลชั่วคราว ไม่แตะข้อมูลจริง) — ใช้โดย install.bat"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from shared.envfile import load_env  # noqa: E402

load_env(os.path.join(ROOT, ".env"))
tmp = tempfile.mkdtemp()
os.environ["HUB_DATABASE"] = os.path.join(tmp, "hub.db")
os.environ["SAFETY_DATABASE"] = os.path.join(tmp, "safety.db")
os.environ["SAFETY_UPLOAD_ROOT"] = os.path.join(tmp, "up")
try:
    from hub.app import create_app as hub_app
    from safety.app import create_app as safety_app
    from safety import report_fonts
    assert hub_app().test_client().get("/login").status_code == 200, "Hub /login"
    app = safety_app()
    assert app.test_client().get("/").status_code in (200, 302), "Safety /"
    print("ตรวจระบบผ่าน (Hub + Safety)" + ("" if report_fonts.available() else " — เตือน: ไม่พบฟอนต์ไทยสำหรับ PDF"))
except Exception as exc:  # noqa: BLE001
    print("ตรวจระบบไม่ผ่าน:", type(exc).__name__, exc)
    sys.exit(1)
