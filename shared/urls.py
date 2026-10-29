"""shared/urls.py — ลิงก์ที่ส่งให้เบราว์เซอร์: ถ้าที่อยู่ที่ตั้งไว้เป็น localhost/127.0.0.1 ให้ใช้ชื่อโฮสต์ที่ผู้ใช้เปิดเข้ามาจริง
(เช่น เปิด Hub จาก LAN ที่ 192.168.1.10:5001 → ลิงก์ไป Safety เป็น 192.168.1.10:5200) — ผู้ดูแลไม่ต้องตั้งที่อยู่เครื่องเอง
ที่อยู่ที่ตั้งเป็นชื่อ/IP จริงแล้วไม่ถูกแก้"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
HOST_RE = re.compile(r"^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9._-]+)(:\d{1,5})?$")


def public_url(url: str, request_host: str | None) -> str:
    parts = urlsplit(url or "")
    if not parts.scheme or (parts.hostname or "").lower() not in LOOPBACK:
        return url
    match = HOST_RE.match((request_host or "").strip())
    if not match:
        return url
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, match.group(1) + port, parts.path, parts.query, parts.fragment))
