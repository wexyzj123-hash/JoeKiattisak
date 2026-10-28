import pytest

from safety import patrol_admin as pa
from safety.db import connect
from safety.schema import ensure_schema
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_user, user_row
from tests.safety.helpers import login


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.a, self.b = add_chain(db, "A"), add_chain(db, "B")
        u = lambda role, name, **kw: user_row(db, add_user(db, role, name, **kw))
        self.admin, self.viewer, self.rep = u("admin", "แอดมิน"), u("viewer", "ผู้ดู"), u("reporter", "ผู้แจ้ง")
        self.insp = u("assessor", "ผู้ตรวจ")
        self.insp_b = u("assessor", "ผู้ตรวจ B", factory_id=self.b["f"])
        self.head = u("dept_head", "หัวหน้า")                           # ไม่มี patrol.perform


@pytest.fixture
def w(db):
    return W(db)


def cl(w, code="CL1", **kw):
    cid, errors = pa.create_checklist(w.db, w.admin, {"code": code, "name_th": "แบบตรวจ", **kw})
    assert errors == [], errors
    return cid


def item(w, cid, **kw):
    iid, errors = pa.add_item(w.db, w.admin, cid, {"section": "หมวด", "text_th": "ข้อตรวจ", **kw})
    assert errors == [], errors
    return iid


def test_create_rename_and_deactivate_checklist(w):
    cid = cl(w, description="รายละเอียด")
    assert pa.update_checklist(w.db, w.admin, cid, {"name_th": "ชื่อใหม่", "description": "ใหม่"}) == []
    row = w.db.execute("SELECT * FROM checklists WHERE id=?", (cid,)).fetchone()
    assert (row["code"], row["name_th"], row["description"], row["active"], row["created_by"]) == ("CL1", "ชื่อใหม่", "ใหม่", 1, w.admin["id"])
    assert pa.toggle_checklist(w.db, w.admin, cid) == [] and w.db.execute("SELECT active FROM checklists").fetchone()[0] == 0
    assert pa.toggle_checklist(w.db, w.admin, cid) == [] and w.db.execute("SELECT active FROM checklists").fetchone()[0] == 1


@pytest.mark.parametrize("form", [{"code": ""}, {"code": "ก ข"}, {"code": "a" * 33}, {"code": "OK", "name_th": ""}, {"code": "OK", "name_th": "ก" * 201},
                                  {"code": "OK", "name_th": "ชื่อ", "description": "ก" * 1001}, {"code": "cl1", "name_th": "ซ้ำรหัส"}])
def test_checklist_validation_and_case_insensitive_duplicate_code(w, form):
    cl(w, "CL1")
    cid, errors = pa.create_checklist(w.db, w.admin, form)
    assert cid is None and errors and w.db.execute("SELECT COUNT(*) FROM checklists").fetchone()[0] == 1


def test_item_limits_and_defaults(w):
    cid = cl(w)
    iid = item(w, cid)
    row = w.db.execute("SELECT * FROM checklist_items WHERE id=?", (iid,)).fetchone()
    assert (row["weight"], row["sort"], row["require_photo_on_fail"], row["active"]) == (1, 0, 0, 1)
    assert item(w, cid, require_photo_on_fail="1", weight="10", sort="-5") and w.db.execute("SELECT MAX(weight) FROM checklist_items").fetchone()[0] == 10
    for bad in ({"text_th": ""}, {"text_th": "ก" * 301}, {"section": "ก" * 101}, {"weight": "0"}, {"weight": "11"}, {"weight": "x"}, {"sort": "abc"}, {"sort": "99999"}):
        assert pa.add_item(w.db, w.admin, cid, {"section": "ห", "text_th": "ข", **bad})[0] is None, bad
    assert pa.add_item(w.db, w.admin, 9999, {"text_th": "x"})[0] is None
    assert w.db.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 2


def test_item_edit_keeps_id_toggle_never_deletes(w):
    cid = cl(w)
    iid = item(w, cid)
    assert pa.update_item(w.db, w.admin, iid, {"section": "ใหม่", "text_th": "ข้อใหม่", "weight": "3", "sort": "2", "require_photo_on_fail": "1"}) == []
    row = w.db.execute("SELECT * FROM checklist_items WHERE id=?", (iid,)).fetchone()
    assert (row["id"], row["section"], row["text_th"], row["weight"], row["sort"], row["require_photo_on_fail"]) == (iid, "ใหม่", "ข้อใหม่", 3, 2, 1)
    assert pa.update_item(w.db, w.admin, iid, {"text_th": "", "weight": "3"}) and pa.update_item(w.db, w.admin, 9999, {"text_th": "x"})
    assert pa.toggle_item(w.db, w.admin, iid) == [] and w.db.execute("SELECT active FROM checklist_items WHERE id=?", (iid,)).fetchone()[0] == 0
    assert w.db.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 1
    assert not hasattr(pa, "delete_item") and not hasattr(pa, "delete_checklist") and not hasattr(pa, "delete_plan")


def test_every_function_requires_manage_permission(w):
    cid = cl(w)
    iid = item(w, cid)
    for user in (w.viewer, w.rep, w.insp):
        assert pa.create_checklist(user and w.db, user, {"code": "X", "name_th": "x"})[1] == ["คุณไม่มีสิทธิ์จัดการแบบตรวจ"]
        assert pa.update_checklist(w.db, user, cid, {"name_th": "x"}) and pa.toggle_checklist(w.db, user, cid)
        assert pa.add_item(w.db, user, cid, {"text_th": "x"})[0] is None and pa.update_item(w.db, user, iid, {"text_th": "x"}) and pa.toggle_item(w.db, user, iid)
        assert pa.create_plan(w.db, user, {})[0] is None
    assert w.db.execute("SELECT name_th FROM checklists WHERE id=?", (cid,)).fetchone()[0] == "แบบตรวจ"


def plan_form(w, cid, **kw):
    return {"checklist_id": str(cid), "risk_area_id": str(w.a["a"]), "frequency": "weekly", "inspector_id": "", **kw}


def test_plan_rules(w):
    cid = cl(w); item(w, cid)
    pid, errors = pa.create_plan(w.db, w.admin, plan_form(w, cid, inspector_id=str(w.insp["id"])))
    assert errors == [] and w.db.execute("SELECT frequency, inspector_id FROM patrol_plans WHERE id=?", (pid,)).fetchone()[:] == ("weekly", w.insp["id"])
    assert pa.create_plan(w.db, w.admin, plan_form(w, cid, frequency="daily"))[0] is None                     # ซ้ำกับแผนที่ใช้งานอยู่
    w.db.execute("UPDATE safety_users SET active=0 WHERE id=?", (w.head["id"],)); w.db.commit()
    other = w.db.execute("INSERT INTO checklists(code,name_th) VALUES('CL2','สอง')").lastrowid
    inactive_cl = w.db.execute("INSERT INTO checklists(code,name_th,active) VALUES('CL3','ปิด',0)").lastrowid
    w.db.commit()
    bad = [plan_form(w, 9999), plan_form(w, inactive_cl), plan_form(w, other, risk_area_id="9999"), plan_form(w, other, risk_area_id="x"),
           plan_form(w, other, frequency="yearly"), plan_form(w, other, inspector_id="9999"), plan_form(w, other, inspector_id=str(w.head["id"])),
           plan_form(w, other, inspector_id=str(w.rep["id"])), plan_form(w, other, inspector_id=str(w.insp_b["id"]))]    # ไม่ active / ไม่มี patrol.perform / นอกขอบเขต
    for form in bad:
        assert pa.create_plan(w.db, w.admin, form)[0] is None, form
    w.db.execute("UPDATE risk_areas SET active=0 WHERE id=?", (w.b["a"],)); w.db.commit()
    assert pa.create_plan(w.db, w.admin, plan_form(w, other, risk_area_id=str(w.b["a"])))[0] is None
    assert w.db.execute("SELECT COUNT(*) FROM patrol_plans").fetchone()[0] == 1


def test_update_and_toggle_plan_including_reactivation_conflict(w):
    cid = cl(w); item(w, cid)
    pid, _ = pa.create_plan(w.db, w.admin, plan_form(w, cid))
    assert pa.update_plan(w.db, w.admin, pid, {"frequency": "monthly", "inspector_id": str(w.insp["id"])}) == []
    assert w.db.execute("SELECT frequency, inspector_id FROM patrol_plans WHERE id=?", (pid,)).fetchone()[:] == ("monthly", w.insp["id"])
    assert pa.update_plan(w.db, w.admin, pid, {"frequency": "weekly", "inspector_id": str(w.rep["id"])}) and pa.update_plan(w.db, w.admin, pid, {"frequency": "x"})
    assert pa.toggle_plan(w.db, w.admin, pid) == []
    second, _ = pa.create_plan(w.db, w.admin, plan_form(w, cid))                                                 # แผนเดิมปิดไปแล้ว สร้างใหม่ได้
    assert second and pa.toggle_plan(w.db, w.admin, pid) != []                                                   # เปิดแผนเก่ากลับ = ซ้ำ ต้องปฏิเสธไม่พัง
    assert w.db.execute("SELECT active FROM patrol_plans WHERE id=?", (pid,)).fetchone()[0] == 0


def test_editing_checklist_after_a_round_started_leaves_its_snapshot_untouched(w):
    cid = cl(w)
    i1, i2 = item(w, cid, text_th="ข้อแรก", weight="2"), item(w, cid, text_th="ข้อสอง")
    rid = w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id) VALUES('PT-1',?,?,?)", (cid, w.a["a"], w.insp["id"])).lastrowid
    for iid, text, wt in ((i1, "ข้อแรก", 2), (i2, "ข้อสอง", 1)):
        w.db.execute("INSERT INTO patrol_answers(round_id, item_id, section, text_th, weight, sort) VALUES(?,?,?,?,?,?)", (rid, iid, "หมวด", text, wt, iid))
    w.db.commit()
    before = [tuple(r) for r in w.db.execute("SELECT * FROM patrol_answers ORDER BY id")]
    pa.update_item(w.db, w.admin, i1, {"section": "เปลี่ยน", "text_th": "ชื่อเปลี่ยน", "weight": "9", "sort": "7"})
    pa.toggle_item(w.db, w.admin, i2); pa.toggle_checklist(w.db, w.admin, cid)
    assert [tuple(r) for r in w.db.execute("SELECT * FROM patrol_answers ORDER BY id")] == before
    assert w.db.execute("SELECT COUNT(*) FROM patrol_answers").fetchone()[0] == 2


# ---------- หน้าจอ ----------
def test_admin_pages_render_and_require_permission(safety_app):
    for path in ("/admin/checklists", "/admin/patrol-plans"):
        assert login(safety_app, "admin").get(path).status_code == 200
        assert login(safety_app, "viewer").get(path).status_code == 403 and login(safety_app, "assessor").get(path).status_code == 403
        assert safety_app.test_client().get(path).status_code == 302


def test_screens_create_edit_toggle_flow_csrf_and_escaping(safety_app):
    c = login(safety_app, "admin")
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con, "Z")
    assert c.post("/admin/checklists", data={"action": "create", "code": "X", "name_th": "x"}).status_code == 400
    evil = "<script>alert(1)</script>"
    r = c.post("/admin/checklists", data={"_csrf": csrf(c), "action": "create", "code": "CL9", "name_th": evil}, follow_redirects=True)
    cid = con.execute("SELECT id FROM checklists WHERE code='CL9'").fetchone()[0]
    assert r.status_code == 200 and evil not in r.get_data(as_text=True) and "&lt;script&gt;" in r.get_data(as_text=True)
    page = c.get(f"/admin/checklists/{cid}")
    assert page.status_code == 200 and "table-stack" in page.get_data(as_text=True)
    c.post(f"/admin/checklists/{cid}", data={"_csrf": csrf(c), "action": "add_item", "section": evil, "text_th": evil, "weight": "2"})
    html = c.get(f"/admin/checklists/{cid}").get_data(as_text=True)
    assert evil not in html and html.count("&lt;script&gt;") >= 2
    iid = con.execute("SELECT id FROM checklist_items").fetchone()[0]
    c.post(f"/admin/checklists/{cid}", data={"_csrf": csrf(c), "action": "toggle_item", "id": iid})
    assert con.execute("SELECT active FROM checklist_items").fetchone()[0] == 0
    r = c.post(f"/admin/checklists/{cid}", data={"_csrf": csrf(c), "action": "delete_item", "id": iid}, follow_redirects=True)
    assert con.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 1 and "ไม่รองรับ" in r.get_data(as_text=True)
    c.post("/admin/patrol-plans", data={"_csrf": csrf(c), "action": "create", "checklist_id": cid, "risk_area_id": ch["a"], "frequency": "daily"})
    assert con.execute("SELECT frequency FROM patrol_plans").fetchone()[0] == "daily"
    assert c.get("/admin/checklists/9999").status_code == 404
    assert "table-stack" in c.get("/admin/patrol-plans").get_data(as_text=True)
