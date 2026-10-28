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


RISK_DDL = """
CREATE TABLE IF NOT EXISTS risk_scales (
    kind        TEXT NOT NULL CHECK (kind IN ('likelihood','severity')),
    level       INTEGER NOT NULL CHECK (level >= 1),
    label_th    TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (kind, level)
);
CREATE TABLE IF NOT EXISTS risk_levels (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name_th           TEXT NOT NULL UNIQUE,
    min_score         INTEGER NOT NULL,
    max_score         INTEGER NOT NULL,
    color             TEXT NOT NULL,
    sla_days          INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    sort              INTEGER NOT NULL DEFAULT 0
);
"""

HAZARD_DDL = """
CREATE TABLE IF NOT EXISTS hazards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    risk_area_id   INTEGER NOT NULL REFERENCES risk_areas(id),
    hazard_type_id INTEGER NOT NULL REFERENCES hazard_types(id),
    source_id      INTEGER NOT NULL REFERENCES hazard_sources(id),
    reported_by    INTEGER NOT NULL REFERENCES safety_users(id),
    reported_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    likelihood     INTEGER,
    severity       INTEGER,
    score          INTEGER,
    risk_level_id  INTEGER REFERENCES risk_levels(id),
    level_name     TEXT,
    level_color    TEXT,
    status         TEXT NOT NULL DEFAULT 'reported'
                   CHECK (status IN ('reported','assessed','assigned','closed','rejected')),
    owner_id       INTEGER REFERENCES safety_users(id),
    due_date       TEXT,
    closing_note   TEXT NOT NULL DEFAULT '',
    reject_reason  TEXT NOT NULL DEFAULT '',
    custom         TEXT NOT NULL DEFAULT '{}',
    updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_hazards_status ON hazards(status);
CREATE INDEX IF NOT EXISTS idx_hazards_area ON hazards(risk_area_id);
CREATE INDEX IF NOT EXISTS idx_hazards_reporter ON hazards(reported_by);
CREATE TABLE IF NOT EXISTS hazard_attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hazard_id   INTEGER NOT NULL REFERENCES hazards(id),
    kind        TEXT NOT NULL CHECK (kind IN ('before','after')),
    stored_name TEXT NOT NULL,
    orig_name   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    uploaded_by INTEGER REFERENCES safety_users(id),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS hazard_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hazard_id   INTEGER NOT NULL REFERENCES hazards(id),
    at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    actor_id    INTEGER REFERENCES safety_users(id),
    kind        TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    note        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS hazard_events_no_update BEFORE UPDATE ON hazard_events
BEGIN SELECT RAISE(ABORT, 'hazard_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS hazard_events_no_delete BEFORE DELETE ON hazard_events
BEGIN SELECT RAISE(ABORT, 'hazard_events is append-only'); END;
"""

DEFAULT_LIKELIHOOD = ("แทบไม่เกิด", "เกิดได้ยาก", "เกิดได้บางครั้ง", "เกิดบ่อย", "เกิดบ่อยมาก")
DEFAULT_SEVERITY = ("เล็กน้อย", "ปานกลาง", "ร้ายแรง", "รุนแรง", "รุนแรงมาก")
DEFAULT_LEVELS = (("ต่ำ", 1, 4, "#2ecc71", 30, 0), ("ปานกลาง", 5, 9, "#f1c40f", 14, 0),
                  ("สูง", 10, 16, "#ff9f1c", 7, 0), ("สูงมาก", 17, 25, "#ff4d4f", 3, 1))


def seed_risk(con) -> None:
    """เกณฑ์ตั้งต้น 5x5 + 4 ระดับ ใส่เฉพาะตารางที่ยังว่าง (ไม่ทับที่แอดมินแก้แล้ว)"""
    if con.execute("SELECT COUNT(*) FROM risk_scales").fetchone()[0] == 0:
        for kind, labels in (("likelihood", DEFAULT_LIKELIHOOD), ("severity", DEFAULT_SEVERITY)):
            con.executemany("INSERT INTO risk_scales(kind, level, label_th) VALUES(?,?,?)",
                            [(kind, i, label) for i, label in enumerate(labels, 1)])
    if con.execute("SELECT COUNT(*) FROM risk_levels").fetchone()[0] == 0:
        con.executemany("INSERT INTO risk_levels(name_th,min_score,max_score,color,sla_days,requires_approval,sort) "
                        "VALUES(?,?,?,?,?,?,?)", [(*lv, i) for i, lv in enumerate(DEFAULT_LEVELS)])
    con.commit()


def _add_column(con, table: str, column: str, ddl: str) -> None:
    if column not in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_schema(con) -> None:
    con.executescript(DDL)
    _add_column(con, "safety_users", "factory_id", "INTEGER")
    _add_column(con, "safety_users", "department_id", "INTEGER")
    con.executescript(MASTER_DDL + ROLES_DDL + RISK_DDL + HAZARD_DDL)
    seed_roles(con)
    seed_risk(con)
    con.commit()
