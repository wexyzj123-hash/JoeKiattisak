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
              + _master("hazard_types") + _master("hazard_sources") + _master("incident_causes"))

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
    ("assessor", "ผู้ประเมิน/จป.", "master.view,hazard.report,hazard.assess,hazard.assign,hazard.close,"
                                  "incident.report,incident.investigate,incident.close,patrol.perform,patrol.view,capa.create,capa.verify,report.view,report.export,report.manage"),
    ("dept_head", "หัวหน้าหน่วยงาน", "master.view,hazard.report,hazard.assign,hazard.close,incident.report,incident.investigate,"
                                     "patrol.view,capa.create,report.view,report.export"),
    ("reporter", "ผู้แจ้ง", "hazard.report,incident.report"),
    ("viewer", "ผู้ดูข้อมูล", "master.view"),
)


REPORT_DDL = """
CREATE TABLE IF NOT EXISTS report_due (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key    TEXT NOT NULL CHECK (report_key IN ('monthly','annual','incidents','capa')),
    department_id INTEGER REFERENCES departments(id),
    frequency     TEXT NOT NULL CHECK (frequency IN ('monthly','yearly')),
    due_day       INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 28),
    due_month     INTEGER CHECK (due_month IS NULL OR due_month BETWEEN 1 AND 12),
    active        INTEGER NOT NULL DEFAULT 1,
    created_by    INTEGER REFERENCES safety_users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_due_active ON report_due(report_key, COALESCE(department_id, 0), frequency) WHERE active = 1;
CREATE TABLE IF NOT EXISTS report_submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_key    TEXT NOT NULL CHECK (report_key IN ('monthly','annual','incidents','capa')),
    department_id INTEGER REFERENCES departments(id),
    period        TEXT NOT NULL,
    submitted_by  INTEGER NOT NULL REFERENCES safety_users(id),
    submitted_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note          TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_submission ON report_submissions(report_key, COALESCE(department_id, 0), period);
CREATE TRIGGER IF NOT EXISTS report_submissions_no_update BEFORE UPDATE ON report_submissions
BEGIN SELECT RAISE(ABORT, 'report_submissions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS report_submissions_no_delete BEFORE DELETE ON report_submissions
BEGIN SELECT RAISE(ABORT, 'report_submissions is append-only'); END;
"""


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
    level_requires_approval INTEGER NOT NULL DEFAULT 0,
    version        INTEGER NOT NULL DEFAULT 0,
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
CREATE TRIGGER IF NOT EXISTS hazard_events_no_replace BEFORE INSERT ON hazard_events
WHEN EXISTS (SELECT 1 FROM hazard_events WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'hazard_events is append-only'); END;
"""

CUSTOM_DDL = """
CREATE TABLE IF NOT EXISTS custom_fields (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hazard_type_id INTEGER REFERENCES hazard_types(id),
    key            TEXT NOT NULL,
    label_th       TEXT NOT NULL,
    type           TEXT NOT NULL CHECK (type IN ('text','number','select','date','checkbox')),
    options        TEXT NOT NULL DEFAULT '',
    required       INTEGER NOT NULL DEFAULT 0,
    sort           INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_custom_fields_key ON custom_fields(COALESCE(hazard_type_id, 0), key);
"""

NOTIFY_EVENTS = ("hazard_reported", "hazard_assessed", "hazard_assigned", "hazard_closed", "hazard_rejected", "due_soon", "overdue",
                 "incident_reported", "incident_assigned", "incident_closed",
                 "capa_assigned", "capa_done", "capa_verified", "capa_reopened", "capa_due_soon", "capa_overdue", "patrol_due", "patrol_overdue",
                 "report_due_soon", "report_overdue")
NOTIFY_CHANNELS = ("inapp", "telegram", "line", "teams", "email")
OUTBOX_CHANNELS = ("telegram", "line", "teams", "email")
OUTBOX_STATUSES = ("pending", "sent", "failed", "dead", "cancelled")

def _rules_table(name: str) -> str:
    return f"""CREATE TABLE IF NOT EXISTS {name} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event       TEXT NOT NULL CHECK (event IN ({",".join(repr(e) for e in NOTIFY_EVENTS)})),
    recipients  TEXT NOT NULL DEFAULT '',
    channels    TEXT NOT NULL DEFAULT 'inapp',
    offset_days INTEGER NOT NULL DEFAULT 0,
    min_score   INTEGER,
    enabled     INTEGER NOT NULL DEFAULT 1
);
"""


NOTIFY_DDL = f"""
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES safety_users(id),
    event      TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    link       TEXT NOT NULL DEFAULT '',
    hazard_id  INTEGER REFERENCES hazards(id),
    dedupe_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    read_at    TEXT,
    UNIQUE (user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read_at);
{_rules_table('notify_rules')}
CREATE TABLE IF NOT EXISTS notify_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT NOT NULL CHECK (channel IN ({",".join(repr(c) for c in OUTBOX_CHANNELS)})),
    target          TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ({",".join(repr(s) for s in OUTBOX_STATUSES)})),
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_try_at     TEXT NOT NULL,
    last_error      TEXT NOT NULL DEFAULT '',
    urgent          INTEGER NOT NULL DEFAULT 0,
    notification_id INTEGER REFERENCES notifications(id),
    dedupe_key      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    sent_at         TEXT,
    UNIQUE (channel, target, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON notify_outbox(status, next_try_at);
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
    job         TEXT NOT NULL,
    run_date    TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    PRIMARY KEY (job, run_date)
);
"""

DEFAULT_SETTINGS = (("quiet_start", "22:00"), ("quiet_end", "06:00"), ("daily_time", "07:30"))
_ALL = "inapp,telegram,line,email"
DEFAULT_RULES = (
    ("hazard_reported", "role:assessor", "inapp,telegram", 0, None),
    ("hazard_assessed", "role:dept_head,role:admin", "inapp,telegram,line", 0, 17),
    ("hazard_assigned", "owner", _ALL, 0, None),
    ("hazard_closed", "reporter", "inapp", 0, None),
    ("hazard_rejected", "reporter", "inapp", 0, None),
    ("due_soon", "owner", "inapp,telegram", 2, None),
    ("overdue", "owner", _ALL, 1, None),
    ("overdue", "owner,role:dept_head", _ALL, 3, None),
    ("overdue", "role:admin,role:dept_head", _ALL, 7, None),
)


def rebuild_notify_rules_if_needed(con) -> None:
    """ตารางกฎเก่ามี CHECK ของรายชื่อเหตุการณ์ที่ตกรุ่น — สร้างใหม่เมื่อรายชื่อใน CHECK ไม่ครบตาม NOTIFY_EVENTS (เฟสไหนเพิ่มเหตุการณ์ก็ทำงานเอง)
    คงรหัส/ค่าที่แอดมินแก้ไว้ และคงลำดับ AUTOINCREMENT (รหัสกฎที่ลบไปแล้วไม่ถูกใช้ซ้ำ — กัน dedupe key ชน); ทำในธุรกรรมเดียว รันซ้ำได้"""
    sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='notify_rules'").fetchone()
    if sql is None or all(f"'{e}'" in sql[0] for e in NOTIFY_EVENTS):
        return
    con.commit()
    seq = con.execute("SELECT seq FROM sqlite_sequence WHERE name='notify_rules'").fetchone()
    con.executescript("BEGIN;" + _rules_table("notify_rules_new")
                      + "INSERT INTO notify_rules_new(id,event,recipients,channels,offset_days,min_score,enabled) "
                        "SELECT id,event,recipients,channels,offset_days,min_score,enabled FROM notify_rules;"
                        "DROP TABLE notify_rules; ALTER TABLE notify_rules_new RENAME TO notify_rules; COMMIT;")
    if seq is not None:
        if con.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='notify_rules'", (seq[0],)).rowcount == 0:
            con.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('notify_rules', ?)", (seq[0],))
        con.commit()


INCIDENT_RULES = (
    ("incident_reported", "role:assessor", "inapp,telegram", 0, None),
    ("incident_assigned", "owner", _ALL, 0, None),
    ("incident_closed", "reporter", "inapp", 0, None),
)


def seed_notify(con) -> None:
    """ค่าตั้งต้นของการแจ้งเตือน — ใส่ครั้งเดียว ไม่ทับ/ไม่เติมกลับสิ่งที่แอดมินแก้หรือลบ"""
    con.executemany("INSERT OR IGNORE INTO app_settings(key, value) VALUES(?,?)", DEFAULT_SETTINGS)
    if con.execute("SELECT 1 FROM app_settings WHERE key='notify_rules_seeded'").fetchone() is None:
        con.executemany("INSERT INTO notify_rules(event, recipients, channels, offset_days, min_score) VALUES(?,?,?,?,?)",
                        DEFAULT_RULES)
        con.execute("INSERT INTO app_settings(key, value) VALUES('notify_rules_seeded', '1')")
    con.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES('require_capa_verified', '0')")
    if con.execute("SELECT 1 FROM app_settings WHERE key='patrol_rules_seeded'").fetchone() is None:
        con.executemany("INSERT INTO notify_rules(event, recipients, channels, offset_days, min_score) VALUES(?,?,?,?,?)", PATROL_RULES)
        con.execute("INSERT INTO app_settings(key, value) VALUES('patrol_rules_seeded', '1')")
    if con.execute("SELECT 1 FROM app_settings WHERE key='report_rules_seeded'").fetchone() is None:
        con.executemany("INSERT INTO notify_rules(event, recipients, channels, offset_days, min_score) VALUES(?,?,?,?,?)", REPORT_RULES)
        con.execute("INSERT INTO app_settings(key, value) VALUES('report_rules_seeded', '1')")
    if con.execute("SELECT 1 FROM app_settings WHERE key='incident_rules_seeded'").fetchone() is None:
        con.executemany("INSERT INTO notify_rules(event, recipients, channels, offset_days, min_score) VALUES(?,?,?,?,?)", INCIDENT_RULES)
        con.execute("INSERT INTO app_settings(key, value) VALUES('incident_rules_seeded', '1')")
    con.commit()


INCIDENT_DDL = """
CREATE TABLE IF NOT EXISTS injury_severities (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT NOT NULL UNIQUE,
    name_th          TEXT NOT NULL,
    rank             INTEGER NOT NULL DEFAULT 0,
    counts_lost_time INTEGER NOT NULL DEFAULT 0,
    active           INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS incidents (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    code                    TEXT NOT NULL UNIQUE,
    kind                    TEXT NOT NULL CHECK (kind IN ('accident','near_miss')),
    occurred_at             TEXT NOT NULL,
    risk_area_id            INTEGER NOT NULL REFERENCES risk_areas(id),
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    immediate_action        TEXT NOT NULL DEFAULT '',
    injury_severity_id      INTEGER REFERENCES injury_severities(id),
    likelihood              INTEGER,
    severity                INTEGER,
    score                   INTEGER,
    risk_level_id           INTEGER REFERENCES risk_levels(id),
    level_name              TEXT,
    level_color             TEXT,
    level_requires_approval INTEGER NOT NULL DEFAULT 0,
    reported_by             INTEGER NOT NULL REFERENCES safety_users(id),
    reported_at             TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    status                  TEXT NOT NULL DEFAULT 'reported'
                            CHECK (status IN ('reported','investigating','actions_set','closed','rejected')),
    owner_id                INTEGER REFERENCES safety_users(id),
    cause_id                INTEGER REFERENCES incident_causes(id),
    root_cause              TEXT NOT NULL DEFAULT '',
    corrective_action       TEXT NOT NULL DEFAULT '',
    due_date                TEXT,
    closing_note            TEXT NOT NULL DEFAULT '',
    reject_reason           TEXT NOT NULL DEFAULT '',
    lost_days               INTEGER,
    related_hazard_id       INTEGER REFERENCES hazards(id),
    version                 INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_area ON incidents(risk_area_id);
CREATE INDEX IF NOT EXISTS idx_incidents_reporter ON incidents(reported_by);
CREATE TABLE IF NOT EXISTS incident_people (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    role        TEXT NOT NULL CHECK (role IN ('injured','witness','involved')),
    name        TEXT NOT NULL,
    employee_no TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_incident_people_inc ON incident_people(incident_id);
CREATE TABLE IF NOT EXISTS incident_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    actor_id    INTEGER REFERENCES safety_users(id),
    kind        TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    note        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS incident_events_no_update BEFORE UPDATE ON incident_events
BEGIN SELECT RAISE(ABORT, 'incident_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS incident_events_no_delete BEFORE DELETE ON incident_events
BEGIN SELECT RAISE(ABORT, 'incident_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS incident_events_no_replace BEFORE INSERT ON incident_events
WHEN EXISTS (SELECT 1 FROM incident_events WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'incident_events is append-only'); END;
CREATE TABLE IF NOT EXISTS incident_attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    kind        TEXT NOT NULL CHECK (kind IN ('before','after')),
    stored_name TEXT NOT NULL,
    orig_name   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    uploaded_by INTEGER REFERENCES safety_users(id),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

# (รหัส, ชื่อ, ลำดับ, นับเป็นอุบัติเหตุหยุดงาน) — มาตรฐานโรงงานทั่วไป แอดมินแก้ได้
DEFAULT_INJURY_SEVERITIES = (
    ("property_only", "ทรัพย์สินเสียหาย (ไม่บาดเจ็บ)", 0, 0), ("first_aid", "ปฐมพยาบาล", 1, 0),
    ("medical", "รักษาพยาบาล (ไม่หยุดงาน)", 2, 0), ("lost_time", "หยุดงาน", 3, 1),
    ("permanent", "ทุพพลภาพถาวร", 4, 1), ("fatality", "เสียชีวิต", 5, 1),
)
DEFAULT_INCIDENT_CAUSES = (("PEOPLE", "คน"), ("MACHINE", "เครื่องจักร/อุปกรณ์"), ("METHOD", "วิธีการทำงาน"),
                           ("MATERIAL", "วัสดุ"), ("ENV", "สภาพแวดล้อม"))


def seed_incidents(con) -> None:
    """ค่าตั้งต้นของโมดูลอุบัติเหตุ — ใส่ครั้งเดียว ไม่ทับ/ไม่เติมกลับสิ่งที่แอดมินแก้หรือลบ"""
    if con.execute("SELECT 1 FROM app_settings WHERE key='injury_severities_seeded'").fetchone() is None:
        con.executemany("INSERT OR IGNORE INTO injury_severities(code, name_th, rank, counts_lost_time) VALUES(?,?,?,?)",
                        DEFAULT_INJURY_SEVERITIES)
        con.execute("INSERT INTO app_settings(key, value) VALUES('injury_severities_seeded', '1')")
    if con.execute("SELECT 1 FROM app_settings WHERE key='incident_causes_seeded'").fetchone() is None:
        con.executemany("INSERT OR IGNORE INTO incident_causes(code, name_th) VALUES(?,?)", DEFAULT_INCIDENT_CAUSES)
        con.execute("INSERT INTO app_settings(key, value) VALUES('incident_causes_seeded', '1')")
    con.commit()


PATROL_DDL = """
CREATE TABLE IF NOT EXISTS checklists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name_th     TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER REFERENCES safety_users(id),
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS checklist_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id          INTEGER NOT NULL REFERENCES checklists(id),
    section               TEXT NOT NULL DEFAULT '',
    text_th               TEXT NOT NULL,
    require_photo_on_fail INTEGER NOT NULL DEFAULT 0,
    weight                INTEGER NOT NULL DEFAULT 1,
    sort                  INTEGER NOT NULL DEFAULT 0,
    active                INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_checklist_items_cl ON checklist_items(checklist_id, sort, id);
CREATE TABLE IF NOT EXISTS patrol_plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    checklist_id INTEGER NOT NULL REFERENCES checklists(id),
    risk_area_id INTEGER NOT NULL REFERENCES risk_areas(id),
    frequency    TEXT NOT NULL CHECK (frequency IN ('daily','weekly','monthly')),
    inspector_id INTEGER REFERENCES safety_users(id),
    active       INTEGER NOT NULL DEFAULT 1,
    created_by   INTEGER REFERENCES safety_users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_patrol_plans_active ON patrol_plans(checklist_id, risk_area_id) WHERE active = 1;
CREATE TABLE IF NOT EXISTS patrol_rounds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    plan_id      INTEGER REFERENCES patrol_plans(id),
    checklist_id INTEGER NOT NULL REFERENCES checklists(id),
    risk_area_id INTEGER NOT NULL REFERENCES risk_areas(id),
    inspector_id INTEGER NOT NULL REFERENCES safety_users(id),
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted')),
    started_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    submitted_at TEXT,
    passed       INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    na           INTEGER NOT NULL DEFAULT 0,
    score_pct    INTEGER,
    version      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_patrol_rounds_area ON patrol_rounds(risk_area_id, status);
CREATE TABLE IF NOT EXISTS patrol_answers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     INTEGER NOT NULL REFERENCES patrol_rounds(id),
    item_id      INTEGER NOT NULL REFERENCES checklist_items(id),
    section      TEXT NOT NULL DEFAULT '',
    text_th      TEXT NOT NULL,
    weight       INTEGER NOT NULL DEFAULT 1,
    require_photo INTEGER NOT NULL DEFAULT 0,
    sort         INTEGER NOT NULL DEFAULT 0,
    result       TEXT CHECK (result IN ('pass','fail','na')),
    note         TEXT NOT NULL DEFAULT '',
    UNIQUE (round_id, item_id)
);
CREATE TABLE IF NOT EXISTS patrol_attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id   INTEGER NOT NULL REFERENCES patrol_answers(id),
    stored_name TEXT NOT NULL,
    orig_name   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    uploaded_by INTEGER REFERENCES safety_users(id),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TRIGGER IF NOT EXISTS patrol_rounds_frozen_upd BEFORE UPDATE ON patrol_rounds WHEN OLD.status = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patrol_rounds_frozen_del BEFORE DELETE ON patrol_rounds WHEN OLD.status = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
DROP TRIGGER IF EXISTS patrol_answers_frozen_upd;
CREATE TRIGGER IF NOT EXISTS patrol_answers_frozen_upd BEFORE UPDATE ON patrol_answers
WHEN (SELECT status FROM patrol_rounds WHERE id = OLD.round_id) = 'submitted' OR (SELECT status FROM patrol_rounds WHERE id = NEW.round_id) = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patrol_answers_frozen_del BEFORE DELETE ON patrol_answers
WHEN (SELECT status FROM patrol_rounds WHERE id = OLD.round_id) = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patrol_answers_frozen_ins BEFORE INSERT ON patrol_answers
WHEN (SELECT status FROM patrol_rounds WHERE id = NEW.round_id) = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
DROP TRIGGER IF EXISTS patrol_attachments_frozen_upd;
CREATE TRIGGER IF NOT EXISTS patrol_attachments_frozen_upd BEFORE UPDATE ON patrol_attachments
WHEN (SELECT r.status FROM patrol_answers a JOIN patrol_rounds r ON r.id = a.round_id WHERE a.id = OLD.answer_id) = 'submitted'
  OR (SELECT r.status FROM patrol_answers a JOIN patrol_rounds r ON r.id = a.round_id WHERE a.id = NEW.answer_id) = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patrol_attachments_frozen_del BEFORE DELETE ON patrol_attachments
WHEN (SELECT r.status FROM patrol_answers a JOIN patrol_rounds r ON r.id = a.round_id WHERE a.id = OLD.answer_id) = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patrol_attachments_frozen_ins BEFORE INSERT ON patrol_attachments
WHEN (SELECT r.status FROM patrol_answers a JOIN patrol_rounds r ON r.id = a.round_id WHERE a.id = NEW.answer_id) = 'submitted'
BEGIN SELECT RAISE(ABORT, 'submitted patrol round is immutable'); END;
"""

CAPA_DDL = """
CREATE TABLE IF NOT EXISTS capas (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    code                 TEXT NOT NULL UNIQUE,
    source_kind          TEXT NOT NULL CHECK (source_kind IN ('hazard','incident','patrol','manual')),
    source_id            INTEGER,
    action_type          TEXT NOT NULL CHECK (action_type IN ('corrective','preventive')),
    title                TEXT NOT NULL,
    description          TEXT NOT NULL DEFAULT '',
    risk_area_id         INTEGER NOT NULL REFERENCES risk_areas(id),
    owner_id             INTEGER NOT NULL REFERENCES safety_users(id),
    due_date             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','done','verified','cancelled')),
    created_by           INTEGER NOT NULL REFERENCES safety_users(id),
    created_at           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    done_by              INTEGER REFERENCES safety_users(id),
    done_at              TEXT,
    done_note            TEXT NOT NULL DEFAULT '',
    verified_by          INTEGER REFERENCES safety_users(id),
    verified_at          TEXT,
    verify_note          TEXT NOT NULL DEFAULT '',
    effectiveness_due    TEXT,
    effectiveness_result TEXT CHECK (effectiveness_result IN ('effective','ineffective')),
    effectiveness_note   TEXT NOT NULL DEFAULT '',
    reopen_count         INTEGER NOT NULL DEFAULT 0,
    cancel_reason        TEXT NOT NULL DEFAULT '',
    version              INTEGER NOT NULL DEFAULT 0,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_capas_status ON capas(status, due_date);
CREATE INDEX IF NOT EXISTS idx_capas_source ON capas(source_kind, source_id);
CREATE INDEX IF NOT EXISTS idx_capas_owner ON capas(owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_capas_patrol_source ON capas(source_id) WHERE source_kind = 'patrol';
CREATE TABLE IF NOT EXISTS capa_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    capa_id     INTEGER NOT NULL REFERENCES capas(id),
    at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    actor_id    INTEGER REFERENCES safety_users(id),
    kind        TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    note        TEXT NOT NULL DEFAULT '',
    data        TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS capa_events_no_update BEFORE UPDATE ON capa_events
BEGIN SELECT RAISE(ABORT, 'capa_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS capa_events_no_delete BEFORE DELETE ON capa_events
BEGIN SELECT RAISE(ABORT, 'capa_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS capa_events_no_replace BEFORE INSERT ON capa_events
WHEN EXISTS (SELECT 1 FROM capa_events WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'capa_events is append-only'); END;
"""

REPORT_RULES = (
    ("report_due_soon", "role:dept_head", "inapp,telegram", 3, None),
    ("report_overdue", "role:dept_head", "inapp,telegram", 1, None),
    ("report_overdue", "role:assessor", "inapp,telegram", 3, None),
)

PATROL_RULES = (
    ("capa_assigned", "owner", _ALL, 0, None),
    ("capa_done", "role:assessor", "inapp", 0, None),
    ("capa_verified", "owner", "inapp", 0, None),
    ("capa_reopened", "owner", "inapp", 0, None),
    ("capa_due_soon", "owner", "inapp,telegram", 2, None),
    ("capa_overdue", "owner", _ALL, 1, None),
    ("capa_overdue", "owner,role:dept_head", _ALL, 3, None),
    ("capa_overdue", "role:admin,role:dept_head", _ALL, 7, None),
    ("patrol_due", "owner", "inapp", 0, None),
    ("patrol_overdue", "owner,role:dept_head", "inapp,telegram", 1, None),
    ("patrol_overdue", "owner,role:dept_head", "inapp,telegram", 5, None),
)

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
    _add_column(con, "safety_users", "telegram_chat_id", "TEXT")
    _add_column(con, "safety_users", "line_user_id", "TEXT")
    _add_column(con, "safety_users", "email", "TEXT")
    _add_column(con, "safety_users", "notify_channels", "TEXT NOT NULL DEFAULT 'inapp'")
    con.executescript(MASTER_DDL + ROLES_DDL + RISK_DDL + HAZARD_DDL + CUSTOM_DDL + NOTIFY_DDL + INCIDENT_DDL + PATROL_DDL + CAPA_DDL + REPORT_DDL)
    _add_column(con, "notifications", "incident_id", "INTEGER")
    _add_column(con, "hazards", "patrol_answer_id", "INTEGER")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_hazards_patrol_answer ON hazards(patrol_answer_id) WHERE patrol_answer_id IS NOT NULL")
    rebuild_notify_rules_if_needed(con)
    seed_roles(con)
    seed_risk(con)
    _add_column(con, "hazards", "level_requires_approval", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "hazards", "version", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "job_runs", "started_at", "TEXT")
    seed_notify(con)
    seed_incidents(con)
    con.commit()
