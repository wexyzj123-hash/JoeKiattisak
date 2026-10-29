"""สร้างโลกสาธิต (ข้อมูลสมมติที่ดูสมจริง) + เปิด Hub/Safety จริง แล้วให้ tools/manual_shots.js จับภาพหน้าจอสำหรับคู่มือ
ใช้: python tools/manual_world.py <โฟลเดอร์ภาพ>   (ต้องมี node + playwright + chromium)"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw
from waitress import create_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def photo(text: str, color) -> bytes:
    img = Image.new("RGB", (900, 600), color)
    d = ImageDraw.Draw(img)
    for i in range(0, 900, 60):
        d.line([(i, 0), (i - 300, 600)], fill=tuple(min(255, c + 25) for c in color), width=18)
    buf = io.BytesIO(); img.save(buf, "JPEG"); return buf.getvalue()


class Live:
    def __init__(self, app):
        self.app = app
        self.server = create_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=self.server.run, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.effective_port}"

    def cookie(self, payload):
        s = self.app.session_interface.get_signing_serializer(self.app).dumps(payload)
        return {"name": self.app.config["SESSION_COOKIE_NAME"], "value": s, "url": self.url}


def main(out: str) -> None:
    from hub.app import create_app as hub_app
    from hub.db import connect as hub_connect
    from hub.security import hash_password
    from safety import capa, images, incident_flow, incidents, patrol, patrol_admin, report_due, workflow
    from safety.app import create_app as safety_app
    from safety.db import connect
    from safety import timeutil
    from tests.safety.factories import user_row

    tmp = tempfile.mkdtemp(prefix="manual-")
    secret = "m" * 40
    hub = hub_app({"TESTING": True, "DATABASE": f"{tmp}/h.db", "SECRET_KEY": "hk", "SSO_SECRET": secret, "SERVICE_TOKEN": "svc", "SAFETY_URL": "http://127.0.0.1:1"})
    saf = safety_app({"TESTING": True, "DATABASE": f"{tmp}/s.db", "SECRET_KEY": "sk", "SSO_SECRET": secret, "SERVICE_TOKEN": "svc",
                      "HUB_URL": "http://127.0.0.1:1", "UPLOAD_ROOT": f"{tmp}/up"})
    hc = hub_connect(hub.config["DATABASE"])
    hc.execute("INSERT INTO users(username,name_th,password_hash,role) VALUES('admin','ผู้ดูแลระบบ',?, 'admin')", (hash_password("Passw0rd!"),))
    hc.execute("INSERT INTO users(username,name_th,password_hash,role) VALUES('somchai','สมชาย ใจดี',?, 'viewer')", (hash_password("Passw0rd!"),))
    hc.commit()
    hub_admin = hc.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]

    con = connect(saf.config["DATABASE"])
    ex = lambda q, a=(): con.execute(q, a).lastrowid
    f = ex("INSERT INTO factories(code,name_th) VALUES('BN','โรงงานบางนา')")
    d1 = ex("INSERT INTO departments(code,name_th,factory_id) VALUES('PD','ฝ่ายผลิต',?)", (f,))
    d2 = ex("INSERT INTO departments(code,name_th,factory_id) VALUES('MT','ฝ่ายซ่อมบำรุง',?)", (f,))
    l1 = ex("INSERT INTO lines(code,name_th,department_id) VALUES('AS1','ไลน์ประกอบ 1',?)", (d1,))
    l2 = ex("INSERT INTO lines(code,name_th,department_id) VALUES('PR2','ไลน์ปั๊มขึ้นรูป 2',?)", (d1,))
    l3 = ex("INSERT INTO lines(code,name_th,department_id) VALUES('WS','ห้องซ่อมบำรุง',?)", (d2,))
    a1 = ex("INSERT INTO risk_areas(code,name_th,line_id) VALUES('AS1-A','จุดป้อนวัตถุดิบ',?)", (l1,))
    a2 = ex("INSERT INTO risk_areas(code,name_th,line_id) VALUES('PR2-A','เครื่องปั๊ม 150 ตัน',?)", (l2,))
    a3 = ex("INSERT INTO risk_areas(code,name_th,line_id) VALUES('WS-A','คลังสารเคมี',?)", (l3,))
    for code, name in (("CHEM", "สารเคมี"), ("MACH", "เครื่องจักร"), ("ELEC", "ไฟฟ้า"), ("FALL", "พื้นลื่น/ตกจากที่สูง"), ("FIRE", "อัคคีภัย")):
        ex("INSERT INTO hazard_types(code,name_th) VALUES(?,?)", (code, name))
    for code, name in (("PATROL", "การเดินตรวจ"), ("EMP", "พนักงานแจ้ง"), ("AUDIT", "การตรวจประเมิน")):
        ex("INSERT INTO hazard_sources(code,name_th) VALUES(?,?)", (code, name))
    con.commit()
    types = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM hazard_types")}
    srcs = {r["code"]: r["id"] for r in con.execute("SELECT id, code FROM hazard_sources")}

    def user(role, name, hub_uid, **kw):
        cur = con.execute("INSERT INTO safety_users(hub_uid,name,role,factory_id,department_id) VALUES(?,?,?,?,?)",
                          (hub_uid, name, role, kw.get("factory_id"), kw.get("department_id")))
        con.commit(); return cur.lastrowid
    admin = user("admin", "ผู้ดูแลระบบ", hub_admin)
    somchai = user("reporter", "สมชาย ใจดี", 101)                                      # ผู้แจ้ง
    wipa = user("assessor", "วิภา รักษ์ปลอดภัย", 102)                                  # จป.
    prasit = user("dept_head", "ประสิทธิ์ มั่นคง", 103, department_id=d1)             # หัวหน้าฝ่ายผลิต
    nattaya = user("assessor", "ณัฐยา สุขสม", 104)                                    # จป. อีกคน (ผู้ยืนยัน)
    U = lambda i: user_row(con, i)

    # รายการอันตราย: ผ่านฟังก์ชันจริงของระบบ
    def hz(title, desc, area, t, s, who=somchai, shots=1):
        hid, errs = workflow.report(con, U(who), {"title": title, "description": desc, "risk_area_id": str(area), "hazard_type_id": str(types[t]), "source_id": str(srcs[s])})
        assert not errs, errs
        for i in range(shots):
            images.save_attachment(con, saf.config["UPLOAD_ROOT"], hid, "before", f"IMG_{i}.jpg", photo("x", (96 + 30 * i, 110, 130)), who)
        return hid
    h1 = hz("น้ำมันรั่วบนพื้นหน้าเครื่องปั๊ม", "พบน้ำมันไฮดรอลิกรั่วเป็นแอ่งกว้างประมาณ 1 ตารางเมตร พื้นลื่นมาก พนักงานเดินผ่านทุกกะ", a2, "FALL", "EMP", shots=2)
    h2 = hz("ถังสารเคมีไม่มีฝาปิด", "ถังโซเดียมไฮดรอกไซด์ 20 ลิตร วางโล่งในคลัง ไม่มีฝาปิดและไม่มีป้ายระบุ", a3, "CHEM", "PATROL")
    h3 = hz("สายไฟต่อพ่วงชำรุด", "สายไฟต่อพ่วงที่ใช้กับสว่านมือ ฉนวนปริแตกเห็นทองแดง", a1, "ELEC", "EMP")
    h4 = hz("การ์ดนิรภัยเครื่องตัดหลุดหาย", "ฝาครอบสายพานด้านข้างเครื่องตัดไม่อยู่ที่", a1, "MACH", "AUDIT")
    h5 = hz("ถังดับเพลิงหมดอายุตรวจสอบ", "ป้ายตรวจสอบเกินกำหนดตั้งแต่เดือนที่แล้ว บริเวณทางออกฉุกเฉิน", a1, "FIRE", "PATROL")
    sc = lambda *a: workflow.assess(con, U(wipa), *a)
    assert sc(h1, 4, 4, "พื้นลื่นในทางเดินหลัก มีผู้ผ่านทุกกะ") == []
    assert sc(h2, 3, 4, "สารกัดกร่อน ต้องปิดภาชนะและติดป้าย") == []
    assert sc(h3, 3, 3) == []
    assert sc(h4, 5, 5, "เครื่องตัดทำงานอยู่ ผู้ปฏิบัติงานเสี่ยงถูกสายพานดึง") == []
    tomorrow = (timeutil.today() + timedelta(days=5)).isoformat()
    assert workflow.assign(con, U(wipa), h1, prasit, tomorrow, "ให้ทำความสะอาดและซ่อมซีลไฮดรอลิก") == []
    assert workflow.assign(con, U(wipa), h4, prasit, "", "") == []
    assert workflow.reject(con, U(wipa), h5, "ซ้ำกับรายการตรวจสอบประจำเดือนที่มีอยู่แล้ว") == []
    con.execute("UPDATE hazards SET due_date=date('now','-2 day') WHERE id=?", (h4,)); con.commit()
    # รายการที่ปิดแล้ว (ให้มีสถิติ)
    h6 = hz("บันไดขึ้นคลังชำรุด", "ขั้นบันไดที่ 3 หัก", a1, "FALL", "EMP", shots=0)
    sc(h6, 2, 2); workflow.assign(con, U(wipa), h6, prasit, "", ""); assert workflow.close(con, U(nattaya), h6, "เปลี่ยนบันไดชุดใหม่ ตรวจรับเรียบร้อย") == []

    # อุบัติเหตุ + Near Miss
    inc1, errs = incidents.report(con, U(somchai), {"kind": "near_miss", "occurred_at": f"{(timeutil.today()-timedelta(days=1)).isoformat()}T09:30", "risk_area_id": str(a2),
        "title": "ชิ้นงานหล่นเกือบถูกเท้าพนักงาน", "description": "ชิ้นงานหล่นจากพาเลทขณะยก ห่างจากเท้า 20 ซม.", "immediate_action": "หยุดงานและจัดเรียงพาเลทใหม่",
        "likelihood": "3", "severity": "4"})
    assert not errs, errs
    sev = lambda c: con.execute("SELECT id FROM injury_severities WHERE code=?", (c,)).fetchone()[0]
    inc2, errs = incidents.report(con, U(somchai), {"kind": "accident", "occurred_at": f"{(timeutil.today()-timedelta(days=3)).isoformat()}T14:10", "risk_area_id": str(a1),
        "title": "นิ้วถูกหนีบขณะเปลี่ยนแม่พิมพ์", "description": "ผู้ปฏิบัติงานนิ้วชี้มือซ้ายถูกหนีบระหว่างแม่พิมพ์ ปฐมพยาบาลแล้วส่งห้องพยาบาล",
        "immediate_action": "ห้ามเลือด ปฐมพยาบาล พาไปห้องพยาบาล", "injury_severity_id": str(sev("medical")), "people_name_0": "สมศักดิ์ แสงทอง", "people_no_0": "E10482", "people_role_0": "injured"})
    assert not errs, errs
    assert incident_flow.assign_investigator(con, U(wipa), inc2, wipa, "", "ให้สอบสวนหาสาเหตุราก") == []
    cause = con.execute("SELECT id FROM incident_causes WHERE code='METHOD'").fetchone()[0]
    assert incident_flow.record_investigation(con, U(wipa), inc2, cause, "ไม่มีขั้นตอนล็อกเครื่องก่อนเปลี่ยนแม่พิมพ์", "จัดทำ LOTO และอบรมผู้ปฏิบัติงานทุกคน", "") == []

    # CAPA
    cp1, errs = capa.create(con, U(wipa), {"title": "จัดทำขั้นตอน LOTO ก่อนเปลี่ยนแม่พิมพ์", "description": "เขียนขั้นตอน ติดป้าย และอบรมทุกกะ", "owner_id": str(prasit),
         "due_date": (timeutil.today() + timedelta(days=21)).isoformat(), "action_type": "preventive"}, ("incident", inc2))
    assert not errs, errs
    cp2, errs = capa.create(con, U(wipa), {"title": "ติดตั้งถาดรองน้ำมันที่เครื่องปั๊ม", "owner_id": str(prasit),
         "due_date": (timeutil.today() + timedelta(days=30)).isoformat(), "action_type": "corrective", "risk_area_id": str(a2)}, ("hazard", h1))
    assert not errs, errs
    capa.start(con, U(prasit), cp2)

    # Safety Patrol
    cl, _ = patrol_admin.create_checklist(con, U(admin), {"code": "PT-DAILY", "name_th": "ตรวจความปลอดภัยประจำสัปดาห์ — ไลน์ผลิต", "description": "ใช้กับเดินตรวจไลน์ผลิตทุกสัปดาห์"})
    items = [("เครื่องจักร", "การ์ดนิรภัยติดตั้งครบ", "3", "1"), ("เครื่องจักร", "ปุ่มหยุดฉุกเฉินใช้งานได้", "3", "1"), ("ความเป็นระเบียบ", "ทางเดินโล่ง ไม่มีของกีดขวาง", "2", ""),
             ("ความเป็นระเบียบ", "พื้นแห้ง ไม่มีคราบน้ำมัน", "2", "1"), ("อัคคีภัย", "ถังดับเพลิงตรวจสอบไม่หมดอายุ", "2", "")]
    for sec, text, w, ph in items:
        patrol_admin.add_item(con, U(admin), cl, {"section": sec, "text_th": text, "weight": w, "require_photo_on_fail": ph})
    patrol_admin.create_plan(con, U(admin), {"checklist_id": str(cl), "risk_area_id": str(a1), "frequency": "weekly", "inspector_id": str(wipa)})
    rid, _ = patrol.start_round(con, U(wipa), checklist_id=cl, risk_area_id=a2)
    ans = con.execute("SELECT id FROM patrol_answers WHERE round_id=? ORDER BY sort", (rid,)).fetchall()
    patrol.save_answers(con, U(wipa), rid, {f"result_{a[0]}": ("fail" if i == 3 else "pass") for i, a in enumerate(ans)} | {f"note_{ans[3][0]}": "พบคราบน้ำมันหน้าเครื่องปั๊ม"})
    images.save_attachment(con, saf.config["UPLOAD_ROOT"], ans[3][0], "before", "oil.jpg", photo("x", (110, 100, 90)), wipa, scope="patrol")
    assert patrol.submit_round(con, U(wipa), rid) == []
    draft, _ = patrol.start_round(con, U(wipa), checklist_id=cl, risk_area_id=a1)

    # กำหนดส่งรายงาน
    for key, freq, extra in (("monthly", "monthly", {"department_id": str(d1)}), ("annual", "yearly", {"due_month": "2"}), ("capa", "monthly", {})):
        assert report_due.save_rule(con, U(admin), {"report_key": key, "frequency": freq, "due_day": "5", **extra}) == []
    con.execute("UPDATE report_due SET created_at=datetime('now','-40 day')"); con.commit()
    con.execute("UPDATE notifications SET created_at=created_at"); con.commit()

    h, s = Live(hub), Live(saf)
    hc.execute("UPDATE app_registry SET url=? WHERE key='safety'", (s.url,)); hc.commit()                     # ให้ Hub ดึงรายชื่อโรงงาน/แผนก/ไลน์จาก Safety ตัวสาธิตได้
    job = {"out": out, "hub": h.url, "safety": s.url, "hub_cookie": h.cookie({"uid": hub_admin}),
           "cookies": {k: s.cookie({"suid": v}) for k, v in (("admin", admin), ("reporter", somchai), ("assessor", wipa), ("head", prasit))},
           "ids": {"h1": h1, "h2": h2, "h3": h3, "h4": h4, "h5": h5, "h6": h6, "inc1": inc1, "inc2": inc2, "cp1": cp1, "cp2": cp2, "round": rid, "draft": draft, "checklist": cl},
           "hubLogin": {"user": "admin", "pass": "Passw0rd!"}}
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ)
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    env["NODE_PATH"] = os.pathsep.join(p for p in (root, "/usr/lib/node_modules", "/usr/local/lib/node_modules") if p)
    r = subprocess.run(["node", os.path.join(ROOT, "tools", "manual_shots.js")], input=json.dumps(job), text=True, env=env)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "manual-shots")
