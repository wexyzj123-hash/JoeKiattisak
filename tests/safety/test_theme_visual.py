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
    con.commit()
    h, sv = Live(hub), Live(saf)
    yield {"hub": h, "safety": sv, "hub_cookie": h.cookie({"uid": hub_uid}), "safe_cookie": sv.cookie({"suid": admin}), "hz": hz, "chain": ch}
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
        P("sf-departments-new", s, "/master/departments/new", sc), P("sf-sso-error", s, "/sso?token=bad"),
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
    assert {r["name"] for r in results if r["stack"]["tables"]} >= {"sf-notify-admin", "sf-users", "sf-custom-fields", "sf-criteria",
                                                                    "sf-hazards", "sf-factories", "hub-admin-users", "hub-admin-apps"}


def test_measured_text_contrast_is_at_least_aa_in_both_themes(results):
    bad = [(r["name"], r["theme"], r["width"], r["contrastFails"]) for r in results if r["contrastFails"]]
    assert not bad, json.dumps(bad, ensure_ascii=False)[:3000]


def test_theme_toggle_persists_and_survives_blocked_storage(world):
    out = run_job({"mode": "toggle", "url": world["hub"].url + "/login", "system": "dark"})["pages"]
    normal, blocked = out
    assert normal["before"] is None and normal["after"] == "light" and normal["afterReload"] == "light" and not normal["errors"]
    assert blocked["after"] == "light" and blocked["afterReload"] is None and not blocked["errors"]             # จำค่าไม่ได้ แต่ไม่พังและยังสลับได้
