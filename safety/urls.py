"""safety/urls.py — ที่อยู่ Hub สำหรับส่งให้เบราว์เซอร์ (ใช้ชื่อโฮสต์ที่ผู้ใช้เปิดเข้ามา ถ้า HUB_URL เป็น localhost) — การเรียกฝั่งเซิร์ฟเวอร์ใช้ HUB_URL เดิม"""
from flask import current_app, request

from shared.urls import public_url


def hub_public(path: str = "") -> str:
    return public_url(current_app.config["HUB_URL"].rstrip("/") + path if path else current_app.config["HUB_URL"], request.host)
