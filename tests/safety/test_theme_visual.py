"""ตรวจหน้าจริงด้วย Chromium (Playwright ผ่าน Node): ไม่ล้นแนวนอนบนมือถือ, สีตัวอักษรที่วัดได้จริง ≥ 4.5:1 ทั้งสองธีม, การสลับธีม
ข้ามอัตโนมัติ (พร้อมเหตุผล) ถ้าไม่มี node/playwright/chromium — ตั้ง VISUAL_SHOTS=<โฟลเดอร์> เพื่อบันทึกภาพหน้าจอไว้ดู"""
import json
import os
import shutil
import subprocess
import threading
from datetime import date

import pytest
from waitress import create_server

RUNNER = os.path.join(os.path.dirname(__file__), "..", "visual", "runner.js")


def _node_env():
    env = dict(os.environ)
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        root = ""
    env["NODE_PATH"] = os.pathsep.join(p for p in (root, env.get("NODE_PATH", ""), "/usr/lib/node_modules", "/usr/local/lib/node_modules") if p)
    return env


def _playwright_available():
    if not shutil.which("node"):
        return "node ไม่ได้ติดตั้ง"
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, text=True, env=_node_env(), timeout=60)
    return None if r.returncode == 0 else "ไม่พบโมดูล playwright ของ Node"


SKIP_REASON = _playwright_available()
pytestmark = [pytest.mark.visual, pytest.mark.skipif(SKIP_REASON is not None, reason=f"ข้ามการตรวจด้วยเบราว์เซอร์: {SKIP_REASON}")]


def run_job(job):
    r = subprocess.run(["node", RUNNER], input=json.dumps(job), capture_output=True, text=True, env=_node_env(), timeout=240)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


class Live:
    """เซิร์ฟเวอร์จริง (waitress) ของ Hub และ Safety บนพอร์ตสุ่ม"""
    def __init__(self, app):
        self.app = app
        self.server = create_server(app, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.effective_port}"

    def cookie(self, payload):
        name = self.app.config["SESSION_COOKIE_NAME"]
        value = self.app.session_interface.get_signing_serializer(self.app).dumps(payload)
        return {"name": name, "value": value, "url": self.url}

    def close(self):
        self.server.close()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from hub.app import create_app as hub_app
    from safety.app import create_app as safety_app
    from safety.db import connect
    from safety import timeutil
    from tests.safety.factories import add_chain, add_hazard, add_refs, add_user
    tmp = tmp_path_factory.mktemp("visual")
    secret = "v" * 40
    hub = hub_app({"TESTING": True, "DATABASE": str(tmp / "h.db"), "SECRET_KEY": "hk", "SSO_SECRET": secret, "SERVICE_TOKEN": "svc", "SAFETY_URL": "http://127.0.0.1:1"})
    saf = safety_app({"TESTING": True, "DATABASE": str(tmp / "s.db"), "SECRET_KEY": "sk", "SSO_SECRET": secret, "SERVICE_TOKEN": "svc",
                      "HUB_URL": "http://127.0.0.1:1", "UPLOAD_ROOT": str(tmp / "up")})
    from hub.db import connect as hub_connect
    from hub.security import hash_password
    hc = hub_connect(hub.config["DATABASE"])
    hc.execute("INSERT INTO users(username,name_th,password_hash,role) VALUES('boss','สมชาย ใจดี',?, 'admin')", (hash_password("Passw0rd!"),)); hc.commit()
    hub_uid = hc.execute("SELECT id FROM users").fetchone()[0]
    con = connect(saf.config["DATABASE"])
    admin = add_user(con, "admin", "ผู้ดูแล ระบบ ที่ชื่อยาวมากมากมากมาก")
    reporter = add_user(con, "reporter", "ผู้แจ้ง")
    ch = add_chain(con)
    t, s = add_refs(con)
    long_title = "สารเคมีรั่วไหลบริเวณจุดป้อนวัตถุดิบสายการผลิตหมายเลขหนึ่ง " + "X" * 70
    hz = {}
    rows = [("reported", {}), ("assessed", dict(likelihood=3, severity=3, score=9, level_name="ปานกลาง", level_color="#f1c40f")),
            ("assigned", dict(likelihood=5, severity=5, score=25, level_name="สูงมาก", level_color="#ff4d4f", level_requires_approval=1,
                              owner_id=admin, due_date="2020-01-01")),
            ("closed", dict(likelihood=1, severity=1, score=1, level_name="ต่ำ", level_color="#2ecc71", closing_note="เรียบร้อย"))]
    for status, kw in rows:
        hz[status] = add_hazard(con, ch["a"], reporter, status=status, title=long_title, description="รายละเอียด " * 30, **kw)
    con.execute("INSERT INTO hazard_events(hazard_id, actor_id, kind, from_status, to_status, note) VALUES(?,?,?,?,?,?)",
                (hz["assigned"], admin, "assigned", "assessed", "assigned", "มอบหมายให้แก้ไข"))
    for i in range(3):
        con.execute("INSERT INTO notifications(user_id,event,title,body,link,dedupe_key) VALUES(?,?,?,?,?,?)",
                    (admin, "overdue", f"เกินกำหนด HZ-{i}", "รายละเอียด " * 20, f"/hazards/{hz['assigned']}", f"k{i}"))
    from safety import incident_flow, incidents
    from tests.safety.factories import severity_id, user_row
    adm, rep = user_row(con, admin), user_row(con, reporter)
    inc = {}
    inc["reported"], _ = incidents.report(con, rep, {"kind": "accident", "occurred_at": "2026-03-01T08:15", "risk_area_id": str(ch["a"]),
        "title": long_title, "description": "รายละเอียด " * 30, "immediate_action": "ปฐมพยาบาล", "injury_severity_id": str(severity_id(con, "lost_time")),
        "people_name_0": "สมชาย ใจดี", "people_no_0": "E1001", "people_name_1": "สมหญิง รักดี", "people_role_1": "witness"})
    inc["closed"], _ = incidents.report(con, rep, {"kind": "near_miss", "occurred_at": "2026-03-02T09:00", "risk_area_id": str(ch["a"]),
        "title": "เกือบชนเสา", "likelihood": "4", "severity": "4"})
    other_admin = user_row(con, add_user(con, "admin", "แอดมินอีกคน"))
    cause = con.execute("SELECT id FROM incident_causes LIMIT 1").fetchone()[0]
    incident_flow.assign_investigator(con, adm, inc["closed"], adm["id"], "")
    incident_flow.record_investigation(con, adm, inc["closed"], cause, "ขาดป้ายเตือน", "ติดป้าย", "")
    incident_flow.close(con, other_admin, inc["closed"], "ติดป้ายแล้ว")
    con.commit()
    from safety import patrol_admin
    clid, _ = patrol_admin.create_checklist(con, adm, {"code": "CL1", "name_th": "ตรวจเครื่องจักรประจำวัน " + "ยาว" * 30})
    for n in range(4):
        patrol_admin.add_item(con, adm, clid, {"section": "หมวดความปลอดภัย", "text_th": f"รายการตรวจที่ {n + 1} " + "ข้อความยาว" * 8, "weight": str(n + 1), "require_photo_on_fail": "1" if n % 2 else ""})
    patrol_admin.create_plan(con, adm, {"checklist_id": str(clid), "risk_area_id": str(ch["a"]), "frequency": "weekly", "inspector_id": ""})
    from safety import capa as capa_mod, patrol as patrol_mod
    insp = user_row(con, add_user(con, "assessor", "ผู้ตรวจประจำ"))
    sub_form = None
    d_round, _ = patrol_mod.start_round(con, adm, checklist_id=clid, risk_area_id=ch["a"])
    d_answers = con.execute("SELECT id FROM patrol_answers WHERE round_id=?", (d_round,)).fetchall()
    patrol_mod.save_answers(con, adm, d_round, {f"result_{d_answers[0][0]}": "pass"})
    s_round, _ = patrol_mod.start_round(con, insp, checklist_id=clid, risk_area_id=ch["a"])
    s_ans = con.execute("SELECT id FROM patrol_answers WHERE round_id=? ORDER BY sort", (s_round,)).fetchall()
    patrol_mod.save_answers(con, insp, s_round, {f"result_{a[0]}": ("fail" if i == 0 else "pass") for i, a in enumerate(s_ans)})
    con.execute("UPDATE checklist_items SET require_photo_on_fail=0"); con.commit()
    errs = patrol_mod.submit_round(con, insp, s_round)
    assert errs == [], errs
    cp_open, _ = capa_mod.create(con, adm, {"title": "ติดตั้งการ์ดเครื่องจักร " + "ยาว" * 20, "description": "รายละเอียด " * 20, "owner_id": str(reporter), "due_date": "2026-12-01",
                                            "action_type": "corrective", "risk_area_id": str(ch["a"])})
    cp_done, _ = capa_mod.create(con, adm, {"title": "ทำเสร็จแล้ว", "owner_id": str(reporter), "due_date": "2026-12-05", "action_type": "preventive", "risk_area_id": str(ch["a"])},
                                 None)
    capa_mod.complete(con, user_row(con, reporter), cp_done, "ดำเนินการแล้ว")
    h, sv = Live(hub), Live(saf)
    yield {"hub": h, "safety": sv, "hub_cookie": h.cookie({"uid": hub_uid}), "safe_cookie": sv.cookie({"suid": admin}), "hz": hz, "inc": inc, "checklist": clid, "d_round": d_round, "s_round": s_round, "cp_open": cp_open, "cp_done": cp_done, "chain": ch}
    h.close(); sv.close()


def pages(world):
    h, s, hz = world["hub"], world["safety"], world["hz"]
    hc, sc = world["hub_cookie"], world["safe_cookie"]
    P = lambda name, base, path, ck=None: {"name": name, "url": base.url + path, **({"cookie": ck} if ck else {})}
    return [
        P("hub-login", h, "/login"), P("hub-launcher", h, "/", hc), P("hub-admin-apps", h, "/admin/apps", hc), P("hub-admin-users", h, "/admin/users", hc),
        P("sf-home", s, "/", sc), P("sf-hazards", s, "/hazards", sc), P("sf-hazards-empty-search", s, "/hazards?q=ไม่มีทางเจอ", sc),
        P("sf-hazard-new", s, "/hazards/new", sc), P("sf-hazard-assigned", s, f"/hazards/{hz['assigned']}", sc),
        P("sf-hazard-reported", s, f"/hazards/{hz['reported']}", sc), P("sf-hazard-closed", s, f"/hazards/{hz['closed']}", sc),
        P("sf-overview", s, "/overview", sc), P("sf-overview-dept", s, "/overview/departments", sc),
        P("sf-notifications", s, "/notifications", sc), P("sf-profile", s, "/profile", sc),
        P("sf-criteria", s, "/admin/criteria", sc), P("sf-custom-fields", s, "/admin/custom-fields", sc), P("sf-roles", s, "/admin/roles", sc),
        P("sf-users", s, "/admin/users", sc), P("sf-notify-admin", s, "/admin/notifications", sc),
        P("sf-factories", s, "/master/factories", sc), P("sf-factory-edit", s, f"/master/factories/{world['chain']['f']}/edit", sc),
        P("sf-departments-new", s, "/master/departments/new", sc),
        P("sf-incidents", s, "/incidents", sc), P("sf-incident-new", s, "/incidents/new", sc),
        P("sf-incident-reported", s, f"/incidents/{world['inc']['reported']}", sc), P("sf-incident-closed", s, f"/incidents/{world['inc']['closed']}", sc),
        P("sf-injury-admin", s, "/admin/injury-severities", sc),
        P("sf-checklists", s, "/admin/checklists", sc), P("sf-checklist-items", s, f"/admin/checklists/{world['checklist']}", sc), P("sf-patrol-plans", s, "/admin/patrol-plans", sc), P("sf-incident-stats", s, "/incidents/stats", sc), P("sf-incident-causes", s, "/master/incident-causes", sc),
        P("sf-patrols", s, "/patrols", sc), P("sf-patrol-draft", s, f"/patrols/{world['d_round']}", sc), P("sf-patrol-submitted", s, f"/patrols/{world['s_round']}", sc),
        P("sf-capas", s, "/capas", sc), P("sf-capa-new", s, "/capas/new", sc), P("sf-capa-open", s, f"/capas/{world['cp_open']}", sc),
        P("sf-capa-done", s, f"/capas/{world['cp_done']}", sc), P("sf-capa-settings", s, "/admin/capa-settings", sc),
        P("sf-sso-error", s, "/sso?token=bad"),
    ]


SHOTS = os.environ.get("VISUAL_SHOTS")


@pytest.fixture(scope="module")
def results(world):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    job = {"themes": ["dark", "light"], "viewports": [{"width": 320, "height": 640}, {"width": 390, "height": 844}, {"width": 768, "height": 900}, {"width": 1280, "height": 800}],
           "pages": pages(world), **({"shotDir": SHOTS} if SHOTS else {})}
    pages_out = run_job(job)["pages"]
    if os.environ.get("VISUAL_REPORT"):                       # เก็บผลดิบไว้วิเคราะห์ (ไม่กระทบการทดสอบ)
        with open(os.environ["VISUAL_REPORT"], "w", encoding="utf-8") as fh:
            json.dump(pages_out, fh, ensure_ascii=False, indent=1)
    return pages_out


def test_every_page_loads_without_errors(results):
    bad = [(r["name"], r["status"], r["pageErrors"]) for r in results if r["status"] not in (200, 403) or r["pageErrors"]]
    assert not bad, bad
    assert {r["status"] for r in results if r["name"] != "sf-sso-error"} == {200}


def test_no_horizontal_scroll_at_any_tested_width(results):
    bad = [(r["name"], r["theme"], r["width"], r["scrollWidth"], r["clientWidth"], r["offenders"]) for r in results
           if r["scrollWidth"] > r["clientWidth"] + 1 or r["offenders"]]
    assert not bad, bad


def test_stacked_tables_are_labelled_and_controls_usable_on_narrow_screens(results):
    assert sum(r["stack"]["tables"] for r in results) > 0
    bad = [(r["name"], r["theme"], r["width"], r["stack"]) for r in results
           if r["stack"]["unlabeled"] or r["stack"]["narrowControls"] or r["stack"]["outside"]]
    assert not bad, json.dumps(bad, ensure_ascii=False)[:3000]
    assert {r["name"] for r in results if r["stack"]["tables"]} >= {"sf-notify-admin", "sf-checklists", "sf-checklist-items", "sf-patrol-plans", "sf-patrols", "sf-capas", "sf-users", "sf-custom-fields", "sf-criteria", "sf-incidents", "sf-injury-admin",
                                                                    "sf-hazards", "sf-factories", "hub-admin-users", "hub-admin-apps"}


def test_measured_text_contrast_is_at_least_aa_in_both_themes(results):
    bad = [(r["name"], r["theme"], r["width"], r["contrastFails"]) for r in results if r["contrastFails"]]
    assert not bad, json.dumps(bad, ensure_ascii=False)[:3000]


def test_theme_toggle_persists_and_survives_blocked_storage(world):
    out = run_job({"mode": "toggle", "url": world["hub"].url + "/login", "system": "dark"})["pages"]
    normal, blocked = out
    assert normal["before"] is None and normal["after"] == "light" and normal["afterReload"] == "light" and not normal["errors"]
    assert blocked["after"] == "light" and blocked["afterReload"] is None and not blocked["errors"]             # จำค่าไม่ได้ แต่ไม่พังและยังสลับได้


def test_table_labels_follow_columns_across_colspan_and_ignore_sort_arrows():
    html = ("<table class='table-stack'><tr><th>รหัส ▲</th><th colspan='2'>ชื่อ</th><th>สถานะ ▼</th></tr>"
            "<tr><td>a</td><td>b</td><td>c</td><td>d</td></tr>"
            "<tr><td colspan='2'>รวม</td><td>x</td><td>y</td></tr>"
            "<tr><td colspan='4'>ว่าง</td></tr></table>")
    out = run_job({"mode": "labels", "html": html, "script": os.path.join(os.path.dirname(__file__), "..", "..", "shared", "theme.js")})
    assert out["pages"][0] == [["รหัส", "ชื่อ", "ชื่อ", "สถานะ", None, "ชื่อ", "สถานะ", None]]
    assert out["hrows"] == 1
