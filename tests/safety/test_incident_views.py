import io
import re
from datetime import datetime

import pytest
from PIL import Image

from safety import incident_flow as flow
from safety import incidents, timeutil
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_user, severity_id, user_row
from tests.safety.helpers import login

NOW = datetime(2026, 3, 10, 14, 30)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
    monkeypatch.setattr(timeutil, "today", lambda: NOW.date())


def png():
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (200, 10, 10)).save(buf, "PNG")
    return buf.getvalue()


class V:
    """ฐานข้อมูลของ safety_app + ผู้ใช้หลายบทบาท + เหตุการณ์ตัวอย่าง"""
    def __init__(self, app):
        self.app = app
        self.con = connect(app.config["DATABASE"])
        self.a, self.b = add_chain(self.con, "A"), add_chain(self.con, "B")
        self.rep = login(app, "reporter")
        self.rep2 = login(app, "reporter")
        self.inv = login(app, "assessor")
        self.inv_b = login(app, "assessor", factory_id=self.b["f"])
        self.viewer = login(app, "viewer")
        self.admin = login(app, "admin")
        self.admin2 = login(app, "admin")
        self.cause = self.con.execute("SELECT id FROM incident_causes LIMIT 1").fetchone()[0]

    def user(self, client):
        return user_row(self.con, client.uid)

    def form(self, **kw):
        base = {"kind": "accident", "occurred_at": "2026-03-09T08:15", "risk_area_id": str(self.a["a"]), "title": "รถตักชนเสา",
                "description": "รายละเอียด", "immediate_action": "ปฐมพยาบาล", "injury_severity_id": str(severity_id(self.con, "first_aid"))}
        base.update(kw)
        return base

    def make(self, reporter=None, **kw):
        iid, errors = incidents.report(self.con, self.user(reporter or self.rep), self.form(**kw))
        assert errors == []
        return iid

    def post(self, client, path, **data):
        data["_csrf"] = csrf(client)
        return client.post(path, data=data)


@pytest.fixture
def v(safety_app):
    return V(safety_app)


def test_list_new_detail_render_for_admin_assessor_and_reporter(v):
    iid = v.make()
    for c in (v.admin, v.inv, v.rep):
        assert c.get("/incidents").status_code == 200 and c.get("/incidents/new").status_code == 200
        assert c.get(f"/incidents/{iid}").status_code == 200, c
    html = v.admin.get("/incidents").get_data(as_text=True)
    assert "IN-2603-0001" in html and "รถตักชนเสา" in html and "อุบัติเหตุ" in html


def test_viewer_and_anonymous_cannot_use_incidents(v, safety_app):
    iid = v.make()
    assert v.viewer.get("/incidents").status_code == 403 and v.viewer.get(f"/incidents/{iid}").status_code == 403
    assert safety_app.test_client().get("/incidents").status_code == 302


def test_reporter_sees_only_own_incidents_and_404_for_others(v):
    mine, theirs = v.make(reporter=v.rep, title="ของฉันเอง"), v.make(reporter=v.rep2, title="ของคนอื่นเอง")
    html = v.rep.get("/incidents").get_data(as_text=True)
    assert "ของฉันเอง" in html and "ของคนอื่นเอง" not in html
    assert v.rep.get(f"/incidents/{theirs}").status_code == 404 and v.inv_b.get(f"/incidents/{mine}").status_code == 404
    assert v.rep.get("/incidents/9999").status_code == 404


def test_people_names_visibility_matrix(v):
    iid = v.make(people_name_0="สมชาย ใจดี", people_no_0="EMP-7788", people_note_0="แขนหัก")
    flow.assign_investigator(v.con, v.user(v.admin), iid, v.inv.uid, "")
    secret = ("สมชาย ใจดี", "EMP-7788", "แขนหัก")
    for client in (v.rep, v.inv, v.admin):                                                           # ผู้แจ้งเอง ผู้สอบสวน แอดมิน
        assert all(s in client.get(f"/incidents/{iid}").get_data(as_text=True) for s in secret[:2]), client
    other = login(v.app, "dept_head")                                                                 # dept_head = investigate → เห็นได้
    assert "สมชาย ใจดี" in other.get(f"/incidents/{iid}").get_data(as_text=True)
    stranger = login(v.app, "reporter")
    assert stranger.get(f"/incidents/{iid}").status_code == 404
    for client in (v.rep, v.inv, v.admin, other):                                                     # รายการ: ไม่แสดงชื่อเลย แสดงจำนวน
        lst = client.get("/incidents").get_data(as_text=True)
        assert not any(s in lst for s in secret)
    detail_rep2 = v.make(reporter=v.rep2, people_name_0="ลับมาก", title="เรื่องของคนอื่น")
    assert "ลับมาก" not in v.rep.get(f"/incidents/{detail_rep2}").get_data(as_text=True)


def test_people_hidden_block_shows_count_for_user_who_can_see_incident_but_not_people(v):
    iid = v.make(people_name_0="ผู้บาดเจ็บ A", people_name_1="ผู้บาดเจ็บ B")
    flow.assign_investigator(v.con, v.user(v.admin), iid, v.rep2.uid if False else v.inv.uid, "")
    owner_only = login(v.app, "reporter")
    v.con.execute("UPDATE incidents SET owner_id=? WHERE id=?", (owner_only.uid, iid)); v.con.commit()   # เห็นเพราะเป็นเจ้าของ ไม่ใช่ผู้มีสิทธิ์
    html = owner_only.get(f"/incidents/{iid}").get_data(as_text=True)
    assert "ผู้บาดเจ็บ A" not in html and "ผู้เกี่ยวข้อง 2 คน" in html


def test_new_form_validation_errors_are_shown_and_nothing_saved(v):
    r = v.post(v.rep, "/incidents/new", **v.form(title=""))
    assert r.status_code == 200 and "กรุณากรอกหัวข้อ" in r.get_data(as_text=True)
    assert v.con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0


def test_new_accident_and_near_miss_via_form_redirect_to_detail(v):
    r = v.post(v.rep, "/incidents/new", **v.form(people_name_0="สมชาย"))
    assert r.status_code == 302 and re.fullmatch(r"/incidents/\d+", r.headers["Location"])
    r = v.post(v.rep, "/incidents/new", **v.form(kind="near_miss", likelihood="3", severity="4", title="เฉียดชน"))
    assert r.status_code == 302
    assert v.con.execute("SELECT level_name FROM incidents WHERE title='เฉียดชน'").fetchone()[0]
    page = v.rep.get(r.headers["Location"]).get_data(as_text=True)
    assert "Near Miss" in page and "เฉียดชน" in page


def test_every_post_requires_csrf_and_permission(v):
    iid = v.make()
    for path in ("/incidents/new", f"/incidents/{iid}/assign", f"/incidents/{iid}/investigate", f"/incidents/{iid}/close",
                 f"/incidents/{iid}/reject", f"/incidents/{iid}/people", f"/incidents/{iid}/photos"):
        assert v.admin.post(path, data={"x": "1"}).status_code == 400, path                         # ไม่มี CSRF
    assert v.post(v.viewer, "/incidents/new", **v.form()).status_code == 403
    for step in ("assign", "investigate", "close", "reject", "people"):
        assert v.post(v.rep, f"/incidents/{iid}/{step}", x="1").status_code == 403, step           # reporter ไม่มี investigate/close
    assert v.post(v.inv_b, f"/incidents/{iid}/reject", reason="x").status_code == 404


def test_workflow_through_the_screens_and_close_by_other_user(v):
    iid = v.make()
    v.post(v.inv, f"/incidents/{iid}/assign", owner_id=str(v.inv.uid), due_date="2026-03-20")
    assert v.con.execute("SELECT status FROM incidents WHERE id=?", (iid,)).fetchone()[0] == "investigating"
    v.post(v.inv, f"/incidents/{iid}/investigate", cause_id=str(v.cause), root_cause="ราก", corrective_action="มาตรการ", lost_days="")
    assert v.con.execute("SELECT status FROM incidents WHERE id=?", (iid,)).fetchone()[0] == "actions_set"
    page = v.inv.get(f"/incidents/{iid}").get_data(as_text=True)
    assert f'action="/incidents/{iid}/close"' not in page and "ผู้สอบสวน" in page                       # ผู้สอบสวนไม่เห็นฟอร์มปิด
    r = v.post(v.admin, f"/incidents/{iid}/close", note="ติดตั้งการ์ดแล้ว")
    assert r.status_code == 302
    done = v.admin.get(f"/incidents/{iid}").get_data(as_text=True)
    assert "ปิดแล้ว" in done and "ติดตั้งการ์ดแล้ว" in done and f'action="/incidents/{iid}/assign"' not in done
    assert "ประวัติการดำเนินการ" in done


def test_close_flash_celebrates_and_refusals_are_flashed(v):
    iid = v.make()
    v.post(v.admin, f"/incidents/{iid}/assign", owner_id=str(v.admin.uid), due_date="")
    v.post(v.admin, f"/incidents/{iid}/investigate", cause_id=str(v.cause), root_cause="ก", corrective_action="ข", lost_days="")
    refused = v.admin.post(f"/incidents/{iid}/close", data={"_csrf": csrf(v.admin), "note": "ปิดเอง"}, follow_redirects=True).get_data(as_text=True)
    assert "ผู้ที่เคยเป็นผู้สอบสวนรายการนี้ปิดรายการเองไม่ได้" in refused and "คุณเคยเป็นผู้สอบสวนรายการนี้" in refused
    assert v.con.execute("SELECT status FROM incidents WHERE id=?", (iid,)).fetchone()[0] == "actions_set"
    done = v.admin2.post(f"/incidents/{iid}/close", data={"_csrf": csrf(v.admin2), "note": "เรียบร้อย"}, follow_redirects=True).get_data(as_text=True)
    assert "ปิดรายการเรียบร้อย" in done and "mascot-celebrate" in done


def test_forms_carry_the_version_and_stale_submit_is_refused(v):
    iid = v.make(people_name_0="เดิม")
    inv = v.inv
    flow.assign_investigator(v.con, v.user(v.admin), iid, v.inv.uid, "")
    page = inv.get(f"/incidents/{iid}").get_data(as_text=True)
    assert len(re.findall(r'name="version" value="1"', page)) >= 2                                    # ฟอร์มสอบสวน + ฟอร์มรายชื่อ
    r = v.post(inv, f"/incidents/{iid}/people", version="0", people_name_0="เก่าค้างหน้า")
    assert "ถูกแก้ไขโดยผู้อื่นแล้ว" in inv.get(f"/incidents/{iid}").get_data(as_text=True)
    assert v.con.execute("SELECT name FROM incident_people WHERE incident_id=?", (iid,)).fetchone()[0] == "เดิม"


def test_closed_incident_card_counts_exclude_rejected_on_stats_page(v):
    for i in range(3):
        v.make(title=f"เหตุ{i}")
    v.con.execute("UPDATE incidents SET status='rejected' WHERE title='เหตุ0'"); v.con.commit()
    html = v.admin.get("/incidents/stats").get_data(as_text=True)
    assert re.search(r'<span class="kpi-n">2</span><span class="kpi-l">อุบัติเหตุทั้งหมด \(ไม่รวมที่ปฏิเสธ 1\)', html)


def test_injury_admin_inputs_are_labelled(v):
    html = v.admin.get("/admin/injury-severities").get_data(as_text=True)
    assert html.count('aria-label="ชื่อ ') >= 6 and html.count('aria-label="ลำดับ ') >= 6


def test_photo_upload_limit_5_serving_whitelist_and_nosniff(v):
    iid = v.make()
    for _ in range(5):
        r = v.rep.post(f"/incidents/{iid}/photos", data={"_csrf": csrf(v.rep), "kind": "before", "photos": (io.BytesIO(png()), "a.png")},
                       content_type="multipart/form-data")
        assert r.status_code == 302
    r = v.rep.post(f"/incidents/{iid}/photos", data={"_csrf": csrf(v.rep), "kind": "before", "photos": (io.BytesIO(png()), "a.png")},
                   content_type="multipart/form-data", follow_redirects=True)
    assert "ไม่เกิน 5" in r.get_data(as_text=True)
    rows = v.con.execute("SELECT * FROM incident_attachments WHERE incident_id=?", (iid,)).fetchall()
    assert len(rows) == 5
    f = v.rep.get(f"/incidents/{iid}/files/{rows[0]['id']}")
    assert f.status_code == 200 and f.mimetype == "image/jpeg" and f.headers["X-Content-Type-Options"] == "nosniff"
    assert v.rep2.get(f"/incidents/{iid}/files/{rows[0]['id']}").status_code == 404                  # ไม่เห็นรายการ → ไม่เห็นรูป
    assert v.rep.get(f"/incidents/{iid}/files/99999").status_code == 404
    v.con.execute("UPDATE incident_attachments SET stored_name='../../etc/passwd' WHERE id=?", (rows[1]["id"],)); v.con.commit()
    assert v.rep.get(f"/incidents/{iid}/files/{rows[1]['id']}").status_code == 404
    assert v.rep.post(f"/incidents/{iid}/photos", data={"_csrf": csrf(v.rep), "kind": "after", "photos": (io.BytesIO(png()), "a.png")},
                      content_type="multipart/form-data").status_code == 403                          # ผู้แจ้งแนบรูปหลังแก้ไขไม่ได้


def test_new_form_with_photos_attaches_them(v):
    r = v.rep.post("/incidents/new", data={**v.form(), "_csrf": csrf(v.rep), "photos": (io.BytesIO(png()), "scene.png")},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    assert v.con.execute("SELECT COUNT(*) FROM incident_attachments").fetchone()[0] == 1
    bad = v.rep.post("/incidents/new", data={**v.form(title="รูปเสีย"), "_csrf": csrf(v.rep), "photos": (io.BytesIO(b"not an image"), "x.png")},
                     content_type="multipart/form-data")
    assert bad.status_code == 200 and v.con.execute("SELECT COUNT(*) FROM incidents WHERE title='รูปเสีย'").fetchone()[0] == 0


def test_xss_in_all_user_fields_escaped(v):
    evil = "<script>alert(1)</script>"
    iid = v.make(title=evil, description=evil, immediate_action=evil, people_name_0=evil, people_note_0=evil)
    flow.assign_investigator(v.con, v.user(v.admin), iid, v.inv.uid, "", evil)
    flow.record_investigation(v.con, v.user(v.inv), iid, v.cause, evil, evil, "")
    flow.close(v.con, v.user(v.admin), iid, evil)
    for path in (f"/incidents/{iid}", "/incidents", "/incidents?q=%3Cscript%3E"):
        html = v.admin.get(path).get_data(as_text=True)
        assert "<script>alert(1)" not in html, path
    assert "&lt;script&gt;" in v.admin.get(f"/incidents/{iid}").get_data(as_text=True)


def test_filters_pagination_and_search(v):
    for i in range(23):
        v.make(title=f"เหตุซ้ำ {i}", kind="near_miss" if i % 2 else "accident", likelihood="2", severity="2")
    html = v.admin.get("/incidents?kind=near_miss").get_data(as_text=True)
    assert html.count("Near Miss</span>") >= 11 and "อุบัติเหตุ</span>" not in html.split("<table")[1]
    page2 = v.admin.get("/incidents?page=2&kind=near_miss&q=เหตุซ้ำ").get_data(as_text=True)
    assert "kind=near_miss" in page2[page2.index("<nav"):] and "q=" in page2[page2.index("<nav"):]
    assert v.admin.get("/incidents?q=%25").status_code == 200 and v.admin.get("/incidents?status=bogus&page=999").status_code == 200
    mine = v.rep.get("/incidents?mine=1")
    assert mine.status_code == 200


def test_nav_contains_incident_items_per_role(v):
    admin = v.admin.get("/").get_data(as_text=True)
    for text in ("รายงานอุบัติเหตุ / Near Miss", "สาเหตุอุบัติเหตุ", "ระดับความรุนแรงการบาดเจ็บ"):
        assert text in admin
    rep = v.rep.get("/").get_data(as_text=True)
    assert "รายงานอุบัติเหตุ / Near Miss" in rep and "สาเหตุอุบัติเหตุ" not in rep


def test_related_hazard_link_and_empty_state(v):
    assert "mascot-" in v.admin.get("/incidents").get_data(as_text=True)                              # ว่าง → มาสคอต
    hid = add_hazard(v.con, v.a["a"], v.rep.uid, title="สภาพอันตรายต้นเหตุ")
    iid = v.make(related_hazard_id=str(hid))
    assert f'href="/hazards/{hid}"' in v.admin.get(f"/incidents/{iid}").get_data(as_text=True)
