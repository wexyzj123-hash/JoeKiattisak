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
    if "link_mode" not in {r[1] for r in con.execute("PRAGMA table_info(app_registry)")}:
        # sso = ส่งผู้ใช้ไป <url>/sso พร้อม token (ระบบที่รองรับ SSO) | link = เปิดที่อยู่ตรง ๆ (ระบบเดิมที่ไม่มี SSO เช่น UT CMMS / E-Checksheet) ไม่ส่ง token
        con.execute("ALTER TABLE app_registry ADD COLUMN link_mode TEXT NOT NULL DEFAULT 'sso'")
    con.commit()


LINK_MODES = ("sso", "link")
LINK_SEEDS = (("UTCMMS_URL", "utcmms", "UT CMMS (ระบบซ่อมบำรุง)", 20), ("ECHECK_URL", "echeck", "E-Checksheet", 30))


def seed_link_apps(con, env) -> None:
    """ใส่ปุ่มเชื่อมไประบบเดิมจากค่าใน .env (UTCMMS_URL, ECHECK_URL) ถ้ายังไม่มี — ไม่ทับค่าที่แอดมินแก้ใน Hub แล้ว; รับเฉพาะ http(s)"""
    for var, key, name, sort in LINK_SEEDS:
        url = (env.get(var) or "").strip()
        if url.lower().startswith(("http://", "https://")) and " " not in url:
            con.execute("INSERT OR IGNORE INTO app_registry(key,name_th,url,icon,sort,link_mode) VALUES(?,?,?,?,?,'link')", (key, name, url, "layers", sort))
    con.commit()


def seed_default_apps(con, safety_url: str) -> None:
    """ใส่ระบบ Safety เป็นรายการแรกถ้ายังไม่มี (ไม่ทับค่าที่แอดมินแก้แล้ว)"""
    con.execute("INSERT OR IGNORE INTO app_registry(key,name_th,url,icon,sort) VALUES('safety',?,?,'shield',10)",
                ("ความปลอดภัย (Safety)", safety_url))
    con.commit()
