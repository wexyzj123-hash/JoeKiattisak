"""สร้างไฟล์ .env พร้อมความลับแบบสุ่ม (ไม่ทับไฟล์เดิม) — ใช้โดย install.bat
ใช้: python tools/make_env.py [ที่อยู่เซิร์ฟเวอร์ เช่น localhost หรือ 192.168.1.10 หรือ safety.company.local]"""
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".env")

TEMPLATE = """# สร้างอัตโนมัติโดย install.bat — ห้ามแจกจ่ายหรือ commit ไฟล์นี้ (เป็นความลับ)
SSO_SECRET={sso}
SERVICE_TOKEN={svc}
HUB_SECRET_KEY={hub}
SAFETY_SECRET_KEY={safety}
HUB_URL=http://{host}:5000
SAFETY_URL=http://{host}:5200
SAFETY_TIMEZONE=Asia/Bangkok

# ช่องทางแจ้งเตือน (ไม่บังคับ — ใส่เฉพาะช่องทางที่ใช้ แล้วเปิดระบบใหม่)
#TELEGRAM_BOT_TOKEN=
#LINE_CHANNEL_TOKEN=
#TEAMS_WEBHOOK_URL=
#SMTP_HOST=
#SMTP_PORT=587
#SMTP_TLS=starttls
#SMTP_USER=
#SMTP_PASSWORD=
#SMTP_FROM=
"""


def main() -> int:
    if os.path.exists(PATH):
        print(".env มีอยู่แล้ว — ไม่แก้ไข")
        return 0
    host = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or "localhost"
    if not all(c.isalnum() or c in ".-" for c in host):
        print("ที่อยู่เซิร์ฟเวอร์ไม่ถูกต้อง (ใช้ได้เฉพาะตัวอักษร ตัวเลข . และ -)")
        return 2
    with open(PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(TEMPLATE.format(sso=secrets.token_hex(32), svc=secrets.token_hex(24), hub=secrets.token_hex(32),
                                 safety=secrets.token_hex(32), host=host))
    try:
        os.chmod(PATH, 0o600)
    except OSError:
        pass
    print(f"สร้าง .env แล้ว (ที่อยู่ {host})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
