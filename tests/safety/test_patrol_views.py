import io
import re
from datetime import date, datetime, timedelta

import pytest
from PIL import Image

from safety import capa, patrol, timeutil
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_incident, add_refs, add_user, severity_id, user_row
from tests.safety.helpers import login

NOW = datetime(2026, 3, 10, 14, 30)


@pytest.fixture(autouse=True)
def fixed(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
    monkeypatch.setattr(timeutil, "today", lambda: NOW.date())


def png(color=(9, 9, 9)):
    buf = io.BytesIO(); Image.new("RGB", (30, 30), color).save(buf, "PNG"); return buf.getvalue()


class V:
    def __init__(self, app):
        self.app = app
        self.con = connect(app.config["DATABASE"])
        self.a, self.b = add_chain(self.con, "A"), add_chain(self.con, "B")
        self.con.execute("INSERT INTO safety_roles(key,name_th,permissions) VALUES('patrol_only','ตรวจล้วน','patrol.perform')")
        self.con.commit()
        self.insp = login(app, "assessor")
        self.plain = login(app, "patrol_only")
        self.other_b = login(app, "assessor", factory_id=self.b["f"])
        self.head = login(app, "dept_head")
        self.rep = login(app, "reporter")
        self.rep2 = login(app, "reporter")
        self.viewer = login(app, "viewer")
        self.admin, self.admin2 = login(app, "admin"), login(app, "admin")
        self.verifier = login(app, "assessor")
        self.type_id, self.source_id = add_refs(self.con)
        self.cl = self.con.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','ตรวจเครื่องจักร')").lastrowid
        for n in (1, 2):
            self.con.execute("INSERT INTO checklist_items(checklist_id, section, text_th, require_photo_on_fail, sort) VALUES(?,?,?,?,?)",
                             (self.cl, "หมวด", f"ข้อ {n}", n == 2, n))
        self.con.commit()

    def user(self, c):
        return user_row(self.con, c.uid)

    def post(self, c, path, files=None, **data):
        data["_csrf"] = csrf(c)
        if files:
            data.update(files)
            return c.post(path, data=data, content_type="multipart/form-data")
        return c.post(path, data=data)

    def start(self, c=None, **kw):
        c = c or self.insp
        rid, errors = patrol.start_round(self.con, self.user(c), **{"checklist_id": self.cl, "risk_area_id": self.a["a"], **kw})
        assert errors == [], errors
        return rid

    def answers(self, rid):
        return self.con.execute("SELECT * FROM patrol_answers WHERE round_id=? ORDER BY sort", (rid,)).fetchall()

    def submitted(self, c=None, results=("fail", "fail")):
        c = c or self.insp
        rid = self.start(c)
        ans = self.answers(rid)
        form = {f"result_{a['id']}": res for a, res in zip(ans, results)}
        assert patrol.save_answers(self.con, self.user(c), rid, form) == []
        if results[1] == "fail":
            assert patrol.add_photo(self.con, self.user(c), rid, ans[1]["id"], "a.png", png(), self.app.config["UPLOAD_ROOT"]) == []
        assert patrol.submit_round(self.con, self.user(c), rid) == []
        return rid, [a["id"] for a in ans]

    def capa(self, **kw):
        form = {"title": "แก้ไข", "owner_id": str(self.rep.uid), "due_date": "2026-03-20", "action_type": "corrective", "risk_area_id": str(self.a["a"]), **kw}
        cid, errors = capa.create(self.con, self.user(self.insp), form)
        assert errors == [], errors
        return cid


@pytest.fixture
def v(safety_app):
    return V(safety_app)


# ---------- รอบตรวจ ----------
def test_patrol_list_and_start_flow_and_permissions(v):
    assert v.insp.get("/patrols").status_code == 200 and v.head.get("/patrols").status_code == 200                      # head มี patrol.view
    assert v.rep.get("/patrols").status_code == 403 and v.viewer.get("/patrols").status_code == 403
    r = v.post(v.insp, "/patrols/start", checklist_id=str(v.cl), risk_area_id=str(v.a["a"]))
    assert r.status_code == 302 and re.fullmatch(r"/patrols/\d+", r.headers["Location"])
    assert v.post(v.head, "/patrols/start", checklist_id=str(v.cl), risk_area_id=str(v.a["a"])).status_code == 403
    assert v.insp.post("/patrols/start", data={"checklist_id": "1"}).status_code == 400                                  # CSRF
    bad = v.post(v.insp, "/patrols/start", checklist_id="999", risk_area_id=str(v.a["a"]))
    assert bad.status_code == 302 and bad.headers["Location"] == "/patrols"


def test_due_plan_appears_with_start_button(v):
    v.con.execute("INSERT INTO patrol_plans(checklist_id, risk_area_id, frequency, created_at) VALUES(?,?, 'daily','2026-03-01 08:00:00')", (v.cl, v.a["a"])); v.con.commit()
    html = v.insp.get("/patrols").get_data(as_text=True)
    assert "ตรวจเครื่องจักร" in html and 'name="plan_id"' in html and "เลยกำหนด 9 วัน" in html
    assert 'name="plan_id"' not in v.head.get("/patrols").get_data(as_text=True)


def test_draft_round_screen_inspector_only_and_touch_friendly_markup(v):
    rid = v.start()
    html = v.insp.get(f"/patrols/{rid}").get_data(as_text=True)
    assert f'action="/patrols/{rid}/save"' in html and html.count('type="radio"') == 6 and 'class="answer-choice' in html and 'name="version" value="0"' in html
    assert 'name="photo_%d"' % v.answers(rid)[1]["id"] in html and "ต้องแนบรูป" in html
    for c in (v.plain, v.head, v.other_b):
        assert c.get(f"/patrols/{rid}").status_code == 404, c
    assert v.rep.get(f"/patrols/{rid}").status_code == 403
    assert v.admin.get(f"/patrols/{rid}").status_code == 200


def test_save_and_submit_through_the_screen_with_validation_messages(v):
    rid = v.start()
    a1, a2 = v.answers(rid)
    r = v.post(v.insp, f"/patrols/{rid}/save", submit="1", version="0", **{f"result_{a1['id']}": "pass", f"result_{a2['id']}": "fail"}, follow_redirects=False)
    page = v.insp.get(f"/patrols/{rid}").get_data(as_text=True)
    assert "ต้องแนบรูปอย่างน้อย 1 รูป" in page and v.con.execute("SELECT status FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0] == "draft"
    version = re.search(r'name="version" value="(\d+)"', page).group(1)
    v.insp.post(f"/patrols/{rid}/save", data={"_csrf": csrf(v.insp), "submit": "1", "version": version, f"result_{a1['id']}": "pass", f"result_{a2['id']}": "fail",
                                                f"photo_{a2['id']}": (io.BytesIO(png()), "evidence.png")}, content_type="multipart/form-data")
    row = v.con.execute("SELECT * FROM patrol_rounds WHERE id=?", (rid,)).fetchone()
    assert (row["status"], row["passed"], row["failed"], row["score_pct"]) == ("submitted", 1, 1, 50)
    done = v.insp.get(f"/patrols/{rid}").get_data(as_text=True)
    assert 'type="radio"' not in done and "ส่งแล้ว" in done and "50" in done and "alt=" in done
    stale = v.post(v.insp, f"/patrols/{rid}/save", version="0", **{f"result_{a1['id']}": "fail"})                      # ส่งไปแล้ว แก้ไม่ได้
    assert v.con.execute("SELECT result FROM patrol_answers WHERE id=?", (a1["id"],)).fetchone()[0] == "pass"


def test_save_without_submit_keeps_draft_and_stale_version_is_refused(v):
    rid = v.start()
    a1, _ = v.answers(rid)
    v.post(v.insp, f"/patrols/{rid}/save", version="0", **{f"result_{a1['id']}": "pass"})
    assert v.con.execute("SELECT result FROM patrol_answers WHERE id=?", (a1["id"],)).fetchone()[0] == "pass"
    v.post(v.insp, f"/patrols/{rid}/save", version="0", **{f"result_{a1['id']}": "fail"})                              # ฟอร์มเก่า (version 0 แต่ตอนนี้ 1)
    assert v.con.execute("SELECT result FROM patrol_answers WHERE id=?", (a1["id"],)).fetchone()[0] == "pass"
    assert "ถูกแก้ไขโดยผู้อื่นแล้ว" in v.insp.get(f"/patrols/{rid}").get_data(as_text=True)


def test_delete_draft_and_every_post_needs_csrf_and_permission(v):
    rid = v.start()
    for path in (f"/patrols/{rid}/save", f"/patrols/{rid}/delete", "/patrols/start"):
        assert v.insp.post(path, data={"x": "1"}).status_code == 400, path
    assert v.post(v.rep, f"/patrols/{rid}/delete").status_code == 403
    assert v.post(v.plain, f"/patrols/{rid}/delete").status_code == 404
    assert v.post(v.insp, f"/patrols/{rid}/delete").status_code == 302
    assert v.con.execute("SELECT COUNT(*) FROM patrol_rounds").fetchone()[0] == 0


def test_submitted_round_summary_visibility_and_finding_buttons(v):
    rid, (a1, a2) = v.submitted()
    own = v.insp.get(f"/patrols/{rid}").get_data(as_text=True)
    assert f'action="/patrols/answers/{a1}/hazard"' in own and f'action="/patrols/answers/{a2}/capa"' in own and "ไม่ผ่าน" in own
    head = v.head.get(f"/patrols/{rid}")
    assert head.status_code == 200 and "/patrols/answers/" not in head.get_data(as_text=True)                                     # ดูได้ แต่สร้างข้อค้นพบไม่ได้
    assert v.plain.get(f"/patrols/{rid}").status_code == 404
    assert v.other_b.get(f"/patrols/{rid}").status_code == 404 and v.rep.get(f"/patrols/{rid}").status_code in (403, 404)


def test_findings_create_hazard_and_capa_once_and_redirect(v):
    rid, (a1, a2) = v.submitted(v.insp)
    r = v.post(v.insp, f"/patrols/answers/{a2}/hazard", hazard_type_id=str(v.type_id), source_id=str(v.source_id))
    hid = v.con.execute("SELECT id FROM hazards WHERE patrol_answer_id=?", (a2,)).fetchone()[0]
    assert r.status_code == 302 and r.headers["Location"] == f"/hazards/{hid}"
    v.post(v.insp, f"/patrols/answers/{a2}/hazard", hazard_type_id=str(v.type_id), source_id=str(v.source_id))
    assert v.con.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 1
    r = v.post(v.insp, f"/patrols/answers/{a1}/capa", owner_id=str(v.rep.uid), due_date="2026-03-20", action_type="corrective", title="")
    cid = v.con.execute("SELECT id FROM capas WHERE source_id=?", (a1,)).fetchone()[0]
    assert r.status_code == 302 and r.headers["Location"] == f"/capas/{cid}"
    assert v.post(v.other_b, f"/patrols/answers/{a1}/capa", owner_id=str(v.rep.uid), due_date="2026-03-20").status_code == 404
    assert v.post(v.head, f"/patrols/answers/{a1}/hazard", hazard_type_id=str(v.type_id), source_id=str(v.source_id)).status_code == 403
    done = v.insp.get(f"/patrols/{rid}").get_data(as_text=True)
    assert f'href="/hazards/{hid}"' in done and f'href="/capas/{cid}"' in done


def test_photo_serving_whitelist_ownership_nosniff(v):
    rid, (a1, a2) = v.submitted()
    att = v.con.execute("SELECT * FROM patrol_attachments WHERE answer_id=?", (a2,)).fetchone()
    f = v.insp.get(f"/patrols/{rid}/files/{att['id']}")
    assert f.status_code == 200 and f.mimetype == "image/jpeg" and f.headers["X-Content-Type-Options"] == "nosniff"
    assert v.head.get(f"/patrols/{rid}/files/{att['id']}").status_code == 200
    assert v.other_b.get(f"/patrols/{rid}/files/{att['id']}").status_code == 404 and v.insp.get(f"/patrols/{rid}/files/99999").status_code == 404
    other_rid, _ = v.submitted(v.admin)
    assert v.insp.get(f"/patrols/{other_rid}/files/{att['id']}").status_code == 404                                      # รูปของอีกรอบ
    v.con.execute("PRAGMA foreign_keys=OFF")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        v.con.execute("UPDATE patrol_attachments SET stored_name='../../etc/passwd' WHERE id=?", (att["id"],))            # แก้ไม่ได้ แม้ SQL ตรง


def test_xss_in_checklist_note_and_capa_fields_escaped(v):
    evil = "<script>alert(1)</script>"
    v.con.execute("UPDATE checklist_items SET text_th=?, section=? WHERE sort=1", (evil, evil)); v.con.commit()
    rid = v.start(v.admin)
    a1, a2 = v.answers(rid)
    v.post(v.admin, f"/patrols/{rid}/save", version="0", **{f"result_{a1['id']}": "fail", f"note_{a1['id']}": evil, f"result_{a2['id']}": "pass"})
    html = v.admin.get(f"/patrols/{rid}").get_data(as_text=True)
    assert evil not in html and html.count("&lt;script&gt;") >= 2


# ---------- CAPA ----------
def test_capa_list_new_detail_and_visibility(v):
    cid = v.capa(title="เปลี่ยนสายไฟ")
    for c in (v.insp, v.verifier, v.admin, v.head, v.rep):
        assert c.get("/capas").status_code == 200 and c.get(f"/capas/{cid}").status_code == 200, c
    assert "เปลี่ยนสายไฟ" in v.insp.get("/capas").get_data(as_text=True)
    assert v.rep2.get("/capas").status_code == 200 and "เปลี่ยนสายไฟ" not in v.rep2.get("/capas").get_data(as_text=True)    # ผู้แจ้งอีกคนไม่เห็น
    assert v.rep2.get(f"/capas/{cid}").status_code == 404 and v.other_b.get(f"/capas/{cid}").status_code == 404
    assert v.viewer.get("/capas").status_code == 403 and v.viewer.get("/capas/new").status_code == 403 and v.rep.get("/capas/new").status_code == 403
    assert v.insp.get("/capas/new").status_code == 200 and v.head.get("/capas/new").status_code == 200


def test_capa_new_form_and_prefill_from_hazard(v):
    r = v.post(v.insp, "/capas/new", title="CAPA ใหม่", description="d", action_type="preventive", owner_id=str(v.rep.uid), due_date="2026-03-25", risk_area_id=str(v.a["a"]))
    assert r.status_code == 302 and v.con.execute("SELECT source_kind, action_type FROM capas").fetchone()[:] == ("manual", "preventive")
    bad = v.post(v.insp, "/capas/new", title="", owner_id=str(v.rep.uid), due_date="2026-03-25", risk_area_id=str(v.a["a"]))
    assert bad.status_code == 200 and "กรุณากรอกหัวข้อ" in bad.get_data(as_text=True)
    hz = add_hazard(v.con, v.a["a"], v.rep.uid, title="ต้นเหตุ")
    page = v.insp.get(f"/capas/new?source=hazard:{hz}").get_data(as_text=True)
    assert "ต้นเหตุ" in page and 'name="source"' in page
    r = v.post(v.insp, "/capas/new", source=f"hazard:{hz}", title="จากอันตราย", owner_id=str(v.rep.uid), due_date="2026-03-25", action_type="corrective")
    assert r.status_code == 302 and v.con.execute("SELECT source_kind, source_id FROM capas WHERE title='จากอันตราย'").fetchone()[:] == ("hazard", hz)
    assert v.insp.get("/capas/new?source=hazard:99999").status_code == 404


def test_capa_actions_through_screens_with_four_eyes_and_versions(v):
    cid = v.capa()
    ver = lambda: re.search(r'name="version" value="(\d+)"', v.rep.get(f"/capas/{cid}").get_data(as_text=True)).group(1)
    assert v.post(v.rep, f"/capas/{cid}/start").status_code == 302
    page = v.rep.get(f"/capas/{cid}").get_data(as_text=True)
    assert f'action="/capas/{cid}/complete"' in page and f'action="/capas/{cid}/verify"' not in page
    stale = v.post(v.rep, f"/capas/{cid}/complete", note="x", version="0")
    assert v.con.execute("SELECT status FROM capas WHERE id=?", (cid,)).fetchone()[0] == "in_progress"
    v.post(v.rep, f"/capas/{cid}/complete", note="ทำแล้ว", version=ver())
    assert v.con.execute("SELECT status FROM capas WHERE id=?", (cid,)).fetchone()[0] == "done"
    assert v.post(v.rep, f"/capas/{cid}/verify", note="x").status_code == 403                                           # เจ้าของไม่มี capa.verify
    v.post(v.admin, f"/capas/{cid}/verify", note="ผ่าน", effectiveness_due="2026-04-01")
    assert v.con.execute("SELECT status FROM capas WHERE id=?", (cid,)).fetchone()[0] == "verified"
    done = v.admin.get(f"/capas/{cid}").get_data(as_text=True)
    assert "ยืนยันแล้ว" in done and "ประวัติการดำเนินการ" in done and "2026-04-01" in done


def test_verify_form_hidden_with_explanation_for_the_person_who_marked_done(v):
    cid = v.capa()
    capa.complete(v.con, v.user(v.admin), cid, "แอดมินกดแทน")
    page = v.admin.get(f"/capas/{cid}").get_data(as_text=True)
    assert f'action="/capas/{cid}/verify"' not in page and "ผู้ที่กดว่าเสร็จ" in page
    assert f'action="/capas/{cid}/verify"' in v.admin2.get(f"/capas/{cid}").get_data(as_text=True)
    assert f'action="/capas/{cid}/reopen"' in v.admin2.get(f"/capas/{cid}").get_data(as_text=True)


def test_capa_posts_need_csrf_and_permission_and_visibility(v):
    cid = v.capa()
    for step in ("start", "complete", "verify", "reopen", "cancel", "reassign", "effectiveness"):
        assert v.insp.post(f"/capas/{cid}/{step}", data={"x": "1"}).status_code == 400, step
    assert v.post(v.viewer, f"/capas/{cid}/start").status_code == 404                                      # ผู้ดูที่ไม่เกี่ยวข้อง: มองไม่เห็นรายการ
    assert v.post(v.rep2, f"/capas/{cid}/start").status_code == 404 and v.post(v.other_b, f"/capas/{cid}/complete", note="x").status_code == 404
    assert v.post(v.rep, f"/capas/{cid}/cancel", reason="x").status_code == 302                                         # เจ้าของยกเลิกไม่ได้ → flash แล้ว redirect
    assert v.con.execute("SELECT status FROM capas WHERE id=?", (cid,)).fetchone()[0] == "open"


def test_capa_filters_overdue_pagination_and_search(v):
    for i in range(23):
        v.capa(title=f"งาน {i}", due_date="2026-03-12")
    old = v.capa(title="เก่า")
    v.con.execute("UPDATE capas SET due_date='2026-03-01' WHERE id=?", (old,)); v.con.commit()
    html = v.admin.get("/capas?overdue=1").get_data(as_text=True)
    assert "เก่า" in html and "งาน 1<" not in html
    page2 = v.admin.get("/capas?page=2&q=งาน").get_data(as_text=True)
    assert "q=" in page2[page2.index("<nav"):]
    assert v.admin.get("/capas?q=%25").status_code == 200 and v.admin.get("/capas?status=bogus&page=999").status_code == 200
    assert v.rep.get("/capas?mine=1").status_code == 200


def test_capa_xss_escaped(v):
    evil = "<script>alert(1)</script>"
    cid = v.capa(title=evil, description=evil)
    capa.complete(v.con, v.user(v.rep), cid, evil)
    html = v.admin.get(f"/capas/{cid}").get_data(as_text=True) + v.admin.get("/capas").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_capa_settings_toggle_gate(v):
    assert v.insp.get("/admin/capa-settings").status_code == 403
    assert v.admin.get("/admin/capa-settings").status_code == 200
    v.post(v.admin, "/admin/capa-settings", require_capa_verified="1")
    assert capa.gate_enabled(v.con) is True
    v.post(v.admin, "/admin/capa-settings")
    assert capa.gate_enabled(v.con) is False
    assert v.admin.post("/admin/capa-settings", data={"require_capa_verified": "1"}).status_code == 400


def test_hazard_and_incident_pages_link_to_capas(v):
    hz = add_hazard(v.con, v.a["a"], v.rep.uid, title="เหตุ")
    cid, _ = capa.create(v.con, v.user(v.insp), {"title": "แก้ไขอันตราย", "owner_id": str(v.rep.uid), "due_date": "2026-03-20", "action_type": "corrective"}, ("hazard", hz))
    html = v.insp.get(f"/hazards/{hz}").get_data(as_text=True)
    assert f'href="/capas/{cid}"' in html and f'href="/capas/new?source=hazard:{hz}"' in html
    assert f'href="/capas/new?source=hazard:{hz}"' not in v.rep.get(f"/hazards/{hz}").get_data(as_text=True)
    inc = add_incident(v.con, v.a["a"], v.rep.uid, injury_severity_id=severity_id(v.con, "first_aid"))
    assert f'href="/capas/new?source=incident:{inc}"' in v.insp.get(f"/incidents/{inc}").get_data(as_text=True)


# ---------- เมนูและแดชบอร์ด ----------
def test_nav_items_per_role(v):
    admin = v.admin.get("/").get_data(as_text=True)
    for text in ("ตรวจความปลอดภัย", "CAPA", "ตั้งค่า CAPA"):
        assert text in admin
    assert 'href="/patrols"' in v.head.get("/").get_data(as_text=True) and 'href="/capas"' in v.rep.get("/").get_data(as_text=True)
    assert 'href="/patrols"' not in v.rep.get("/").get_data(as_text=True)


def test_dashboard_latest_rounds_table_and_capa_overdue_kpi(v):
    empty = v.admin.get("/").get_data(as_text=True)
    assert "การตรวจความปลอดภัยล่าสุด" in empty and "ยังไม่มีการตรวจ" in empty and re.search(r'data-kpi="capa-overdue"[^>]*>0<', empty)
    rid, _ = v.submitted(v.insp, ("pass", "pass"))
    draft = v.start(v.admin)
    html = v.admin.get("/").get_data(as_text=True)
    section = html[html.index("การตรวจความปลอดภัยล่าสุด"):]
    row = v.con.execute("SELECT code FROM patrol_rounds WHERE id=?", (rid,)).fetchone()[0]
    drow = v.con.execute("SELECT code FROM patrol_rounds WHERE id=?", (draft,)).fetchone()[0]
    assert row in section and drow not in section and "100" in section
    assert row not in v.other_b.get("/").get_data(as_text=True) and row not in v.rep.get("/").get_data(as_text=True)
    cid = v.capa(due_date="2026-03-12")
    v.con.execute("UPDATE capas SET due_date='2026-03-01' WHERE id=?", (cid,)); v.con.commit()
    assert re.search(r'data-kpi="capa-overdue"[^>]*>1<', v.admin.get("/").get_data(as_text=True))
    assert re.search(r'data-kpi="capa-overdue"[^>]*>1<', v.rep.get("/").get_data(as_text=True))                      # เจ้าของเห็นของตน
    assert len(re.findall(r'class="kpi[ "]', v.admin.get("/").get_data(as_text=True))) == 6
