"""shared/envfile.py — อ่านไฟล์ .env แบบ KEY=VALUE (ไม่ทับค่า environment ที่ตั้งไว้แล้ว)"""
from __future__ import annotations

import os


def load_env(path: str) -> None:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and " " not in key:
            os.environ.setdefault(key, value)


PLACEHOLDER_PREFIX = "change-me"       # ค่าตัวอย่างใน .env.example — ห้ามใช้จริง
MIN_SECRET = 32


def validate_secrets(sso_secret: str, service_token: str) -> None:
    """หยุดเริ่มระบบถ้า SSO_SECRET สั้นเกินไป หรือใช้ค่าตัวอย่างจาก .env.example (ใครอ่านรีโปก็ปลอม token ได้)"""
    if len(sso_secret) < MIN_SECRET:
        raise RuntimeError(f"SSO_SECRET ต้องยาวอย่างน้อย {MIN_SECRET} ตัวอักษร (ตั้งในไฟล์ .env)")
    for name, value in (("SSO_SECRET", sso_secret), ("SERVICE_TOKEN", service_token)):
        if value.startswith(PLACEHOLDER_PREFIX):
            raise RuntimeError(f"{name} ยังเป็นค่าตัวอย่างจาก .env.example — กรุณาตั้งเป็นค่าสุ่มของคุณเอง")
