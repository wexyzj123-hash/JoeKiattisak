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
