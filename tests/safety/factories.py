"""ตัวช่วยสร้างข้อมูลทดสอบผ่าน connection ตรง ๆ (ไม่ผ่านหน้าจอ)"""
from __future__ import annotations


def add_user(con, role="admin", name="ผู้ใช้", factory_id=None, department_id=None, active=1) -> int:
    hub_uid = con.execute("SELECT COALESCE(MAX(hub_uid),0)+1 FROM safety_users").fetchone()[0]
    cur = con.execute("INSERT INTO safety_users(hub_uid,name,role,active,factory_id,department_id) VALUES(?,?,?,?,?,?)",
                      (hub_uid, name, role, active, factory_id, department_id))
    con.commit()
    return cur.lastrowid


def user_row(con, uid):
    return con.execute("SELECT * FROM safety_users WHERE id=?", (uid,)).fetchone()


def add_chain(con, n="1", active=1) -> dict:
    """โรงงาน→แผนก→ไลน์→พื้นที่เสี่ยง หนึ่งสาย; คืน {'f','d','l','a'}"""
    f = con.execute("INSERT INTO factories(code,name_th,active) VALUES(?,?,?)", (f"F{n}", f"โรงงาน {n}", active)).lastrowid
    d = con.execute("INSERT INTO departments(code,name_th,factory_id) VALUES(?,?,?)", (f"D{n}", f"แผนก {n}", f)).lastrowid
    l = con.execute("INSERT INTO lines(code,name_th,department_id) VALUES(?,?,?)", (f"L{n}", f"ไลน์ {n}", d)).lastrowid
    a = con.execute("INSERT INTO risk_areas(code,name_th,line_id) VALUES(?,?,?)", (f"R{n}", f"พื้นที่ {n}", l)).lastrowid
    con.commit()
    return {"f": f, "d": d, "l": l, "a": a}


def add_refs(con) -> tuple[int, int]:
    """ประเภทอันตราย + แหล่งที่มา อย่างละหนึ่ง (ใช้ซ้ำได้ถ้ามีแล้ว)"""
    t = con.execute("SELECT id FROM hazard_types WHERE code='T1'").fetchone()
    s = con.execute("SELECT id FROM hazard_sources WHERE code='S1'").fetchone()
    if t is None:
        t = (con.execute("INSERT INTO hazard_types(code,name_th) VALUES('T1','สารเคมี')").lastrowid,)
    if s is None:
        s = (con.execute("INSERT INTO hazard_sources(code,name_th) VALUES('S1','เครื่องจักร')").lastrowid,)
    con.commit()
    return t[0], s[0]


def add_hazard(con, area_id, reporter_id, code=None, status="reported", **kw) -> int:
    t, s = add_refs(con)
    code = code or f"HZ-TEST-{con.execute('SELECT COUNT(*)+1 FROM hazards').fetchone()[0]:04d}"
    cols = {"code": code, "title": "หัวข้อ", "description": "", "risk_area_id": area_id, "hazard_type_id": t,
            "source_id": s, "reported_by": reporter_id, "status": status, **kw}
    cur = con.execute(f"INSERT INTO hazards({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
    con.commit()
    return cur.lastrowid
