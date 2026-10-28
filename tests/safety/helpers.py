from safety.db import connect


def make_user(app, role="admin", hub_uid=None, name="ทดสอบ", active=1) -> int:
    con = connect(app.config["DATABASE"])
    try:
        if hub_uid is None:
            hub_uid = con.execute("SELECT COALESCE(MAX(hub_uid),0)+1 FROM safety_users").fetchone()[0]
        cur = con.execute("INSERT INTO safety_users(hub_uid,name,role,active) VALUES(?,?,?,?)",
                          (hub_uid, name, role, active))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def login(app, role="admin", **kw):
    """client ที่ล็อกอินเป็นผู้ใช้ Safety ใหม่ (ข้าม SSO) — คืน client; รหัสผู้ใช้อยู่ที่ client.uid"""
    uid = make_user(app, role, **kw)
    c = app.test_client()
    with c.session_transaction() as s:
        s["suid"] = uid
    c.uid = uid
    return c


def sql(app, q, args=()):
    con = connect(app.config["DATABASE"])
    try:
        rows = con.execute(q, args).fetchall()
        con.commit()
        return rows
    finally:
        con.close()
