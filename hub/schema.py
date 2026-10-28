"""hub/schema.py — ตารางของ Hub (รันซ้ำได้ ไม่ทำลายข้อมูลเดิม)"""
from __future__ import annotations

DDL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    name_th       TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer'
                  CHECK (role IN ('admin','supervisor','technician','viewer')),
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS app_registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT NOT NULL UNIQUE,
    name_th       TEXT NOT NULL,
    url           TEXT NOT NULL,
    icon          TEXT NOT NULL DEFAULT 'app',
    enabled       INTEGER NOT NULL DEFAULT 1,
    required_role TEXT NOT NULL DEFAULT '',
    sort          INTEGER NOT NULL DEFAULT 0
);
"""


def ensure_schema(con) -> None:
    con.executescript(DDL)
    con.commit()


def seed_default_apps(con, safety_url: str) -> None:
    """ใส่ระบบ Safety เป็นรายการแรกถ้ายังไม่มี (ไม่ทับค่าที่แอดมินแก้แล้ว)"""
    con.execute("INSERT OR IGNORE INTO app_registry(key,name_th,url,icon,sort) VALUES('safety',?,?,'shield',10)",
                ("ความปลอดภัย (Safety)", safety_url))
    con.commit()
