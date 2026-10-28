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
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    key       TEXT NOT NULL UNIQUE,
    zone      TEXT NOT NULL CHECK (zone IN ('env','utility')),
    name_th   TEXT NOT NULL,
    unit      TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'lower' CHECK (direction IN ('lower','higher')),
    target    REAL,
    decimals  INTEGER NOT NULL DEFAULT 0 CHECK (decimals BETWEEN 0 AND 4),
    sort      INTEGER NOT NULL DEFAULT 0,
    active    INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS metric_readings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id  INTEGER NOT NULL REFERENCES metrics(id),
    read_date  TEXT NOT NULL,
    value      REAL NOT NULL,
    entered_by INTEGER,
    entered_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (metric_id, read_date)
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


def _alive(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# ระบบเดิมบนเครื่องเดียวกัน (พอร์ต 5000): ถ้าไม่ได้ตั้ง URL ไว้และพบว่ามีโปรแกรมฟังอยู่ที่ 5000 จะเพิ่มปุ่มให้เอง (ใช้ localhost แล้วระบบสลับเป็นที่อยู่ที่ผู้ใช้เปิดเข้ามา)
LEGACY_DEFAULTS = {"utcmms": "http://localhost:5000/", "echeck": "http://localhost:5000/echeck/dashboard"}


def seed_link_apps(con, env, hub_port: int | None = None, probe=_alive) -> None:
    """ใส่ปุ่มเชื่อมไประบบเดิมจาก .env (UTCMMS_URL, ECHECK_URL) หรือค้นหาเองที่พอร์ต 5000 — ไม่ทับ/ไม่เปิดกลับค่าที่แอดมินแก้ใน Hub แล้ว; รับเฉพาะ http(s)"""
    try:
        port = int(hub_port if hub_port is not None else (env.get("HUB_PORT") or 5000))
    except ValueError:
        port = 5000
    auto = port != 5000 and probe(5000)
    for var, key, name, sort in LINK_SEEDS:
        url = (env.get(var) or "").strip()
        if not url and auto:
            url = LEGACY_DEFAULTS[key]
        if url.lower().startswith(("http://", "https://")) and " " not in url:
            con.execute("INSERT OR IGNORE INTO app_registry(key,name_th,url,icon,sort,link_mode) VALUES(?,?,?,?,?,'link')", (key, name, url, "layers", sort))
    con.commit()


def seed_default_apps(con, safety_url: str) -> None:
    """ใส่ระบบ Safety เป็นรายการแรกถ้ายังไม่มี (ไม่ทับค่าที่แอดมินแก้แล้ว)"""
    con.execute("INSERT OR IGNORE INTO app_registry(key,name_th,url,icon,sort) VALUES('safety',?,?,'shield',10)",
                ("ความปลอดภัย (Safety)", safety_url))
    con.commit()
