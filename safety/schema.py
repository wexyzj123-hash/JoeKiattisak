"""safety/schema.py — ตารางของ Safety (รันซ้ำได้ ไม่ทำลายข้อมูลเดิม)"""
from __future__ import annotations

from .perms import PERMISSIONS

DDL = """
CREATE TABLE IF NOT EXISTS safety_users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hub_uid    INTEGER NOT NULL UNIQUE,
    name       TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'reporter',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS sso_nonces (
    nonce TEXT PRIMARY KEY,
    exp   REAL NOT NULL
);
"""


def _master(table: str, parent: str = "", parent_table: str = "") -> str:
    """ตารางข้อมูลหลักแบบเดียวกันทุกตาราง: รหัสไม่ซ้ำ (ไม่สนตัวพิมพ์), ปิดใช้งานแทนการลบ"""
    parent_col = f"    {parent} INTEGER NOT NULL REFERENCES {parent_table}(id),\n" if parent else ""
    return f"""CREATE TABLE IF NOT EXISTS {table} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name_th     TEXT NOT NULL,
{parent_col}    description TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER REFERENCES safety_users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


MASTER_DDL = (_master("factories") + _master("departments", "factory_id", "factories")
              + _master("lines", "department_id", "departments") + _master("risk_areas", "line_id", "lines")
              + _master("hazard_types") + _master("hazard_sources"))

ROLES_DDL = """
CREATE TABLE IF NOT EXISTS safety_roles (
    key         TEXT PRIMARY KEY,
    name_th     TEXT NOT NULL,
    permissions TEXT NOT NULL DEFAULT '',
    is_system   INTEGER NOT NULL DEFAULT 0
);
"""

DEFAULT_ROLES = (
    ("admin", "ผู้ดูแลระบบ", ",".join(PERMISSIONS)),
    ("assessor", "ผู้ประเมิน/จป.", "master.view,hazard.report,hazard.assess,hazard.assign,hazard.close"),
    ("dept_head", "หัวหน้าหน่วยงาน", "master.view,hazard.report,hazard.assign,hazard.close"),
    ("reporter", "ผู้แจ้ง", "hazard.report"),
    ("viewer", "ผู้ดูข้อมูล", "master.view"),
)


def seed_roles(con) -> None:
    """ใส่บทบาทตั้งต้น 5 บทบาทเมื่อยังไม่มี (ไม่ทับที่แอดมินแก้แล้ว)"""
    for key, name, perms in DEFAULT_ROLES:
        con.execute("INSERT OR IGNORE INTO safety_roles(key,name_th,permissions,is_system) VALUES(?,?,?,1)",
                    (key, name, perms))
    con.commit()


def _add_column(con, table: str, column: str, ddl: str) -> None:
    if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema(con) -> None:
    con.executescript(DDL)
    _add_column(con, "safety_users", "factory_id", "INTEGER")
    _add_column(con, "safety_users", "department_id", "INTEGER")
    con.executescript(MASTER_DDL + ROLES_DDL)
    seed_roles(con)
    con.commit()
