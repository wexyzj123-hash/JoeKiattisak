import sqlite3

import pytest

from safety.db import connect
from safety.perms import PERMISSIONS
from safety.schema import ensure_schema, seed_roles

MASTER = ["factories", "departments", "lines", "risk_areas", "hazard_types", "hazard_sources"]


def fresh(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


def test_ensure_schema_twice_is_noop(tmp_path):
    con = fresh(tmp_path)
    before = con.execute("SELECT COUNT(*) FROM safety_roles").fetchone()[0]
    ensure_schema(con)
    assert con.execute("SELECT COUNT(*) FROM safety_roles").fetchone()[0] == before == 5
    for t in MASTER:
        con.execute(f"SELECT id, code, name_th, description, active, created_by, created_at, updated_at FROM {t}")


def test_default_roles_seeded_with_admin_all_permissions(tmp_path):
    con = fresh(tmp_path)
    keys = [r["key"] for r in con.execute("SELECT key FROM safety_roles ORDER BY key")]
    assert keys == ["admin", "assessor", "dept_head", "reporter", "viewer"]
    admin = con.execute("SELECT permissions, is_system FROM safety_roles WHERE key='admin'").fetchone()
    assert set(admin["permissions"].split(",")) == set(PERMISSIONS) and admin["is_system"] == 1
    assert set(PERMISSIONS) == {"master.view", "master.edit", "users.manage", "roles.manage", "hazard.report",
                                "hazard.assess", "hazard.assign", "hazard.close", "criteria.manage",
                                "incident.report", "incident.investigate", "incident.close",
                                "patrol.manage", "patrol.perform", "patrol.view", "capa.create", "capa.verify"}


def test_seed_does_not_overwrite_edited_role(tmp_path):
    con = fresh(tmp_path)
    con.execute("UPDATE safety_roles SET name_th='ดูอย่างเดียว', permissions='master.view,hazard.report' WHERE key='viewer'")
    con.commit()
    seed_roles(con)
    r = con.execute("SELECT name_th, permissions FROM safety_roles WHERE key='viewer'").fetchone()
    assert (r["name_th"], r["permissions"]) == ("ดูอย่างเดียว", "master.view,hazard.report")


def test_phase1_database_upgraded_in_place(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE safety_users (id INTEGER PRIMARY KEY AUTOINCREMENT, hub_uid INTEGER NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'reporter', active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        INSERT INTO safety_users(hub_uid, name, role) VALUES (7, 'สมชาย', 'admin');""")
    old.commit()
    old.close()
    con = connect(path)
    ensure_schema(con)
    row = con.execute("SELECT name, role, factory_id, department_id FROM safety_users").fetchone()
    assert (row["name"], row["role"], row["factory_id"], row["department_id"]) == ("สมชาย", "admin", None, None)


@pytest.mark.parametrize("table", ["hazard_types", "factories"])
def test_code_unique_case_insensitive(tmp_path, table):
    con = fresh(tmp_path)
    con.execute(f"INSERT INTO {table}(code, name_th) VALUES('ab-01','x')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(f"INSERT INTO {table}(code, name_th) VALUES('AB-01','y')")
