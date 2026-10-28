import io
import json
import os
import re
from datetime import date, timedelta

import pytest
from PIL import Image

from safety import timeutil, workflow
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_refs, user_row
from tests.safety.helpers import login, make_user

TODAY = date(2026, 3, 10)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timeutil, "today", lambda: TODAY)


def png(color=(200, 30, 30), size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class Env:
    def __init__(self, app):
        self.app = app
        self.db = connect(app.config["DATABASE"])
        self.a, self.b = add_chain(self.db, "A"), add_chain(self.db, "B")
        self.t, self.s = add_refs(self.db)

    def form(self, **kw):
        return {"title": "น้ำมันหกพื้น", "description": "ลื่นล้ม", "risk_area_id": str(self.a["a"]),
                "hazard_type_id": str(self.t), "source_id": str(self.s), **kw}

    def report(self, client, **kw):
        r = client.post("/hazards/new", data={"_csrf": csrf(client), **self.form(**kw)})
        assert r.status_code == 302, r.get_data(as_text=True)[:500]
        return int(r.headers["Location"].rstrip("/").split("/")[-1])

    def row(self, hid):
        return self.db.execute("SELECT * FROM hazards WHERE id=?", (hid,)).fetchone()

    def act(self, client, hid, action, **data):
        return client.post(f"/hazards/{hid}/{action}", data={"_csrf": csrf(client), **data})


@pytest.fixture
def env(safety_app):
    return Env(safety_app)


def test_list_requires_login_and_hazard_permission_or_master_view(env):
    app = env.app
    assert app.test_client().get("/hazards").status_code == 302
    for role in ("reporter", "viewer", "assessor", "admin"):
        assert login(app, role).get("/hazards").status_code == 200, role
    c = login(app, "admin")
    c.post("/admin/roles", data={"_csrf": csrf(c), "action": "create", "key": "hr_only", "name_th": "ฝ่ายบุคคล",
                                 "permissions": ["users.manage"]})
    assert login(app, "hr_only").get("/hazards").status_code == 403
    assert login(app, "hr_only").get("/hazards/new").status_code == 403


def test_report_form_and_submit_with_photo_and_custom_field(env):
    env.db.execute("INSERT INTO custom_fields(hazard_type_id,key,label_th,type,required) VALUES(?,?,?,?,1)",
                   (env.t, "temp", "อุณหภูมิ (°C)", "number"))
    env.db.commit()
    c = login(env.app, "reporter")
    html = c.get("/hazards/new").get_data(as_text=True)
    assert "พื้นที่ A" in html and "สารเคมี" in html and "อุณหภูมิ (°C)" in html and "โรงงาน A › แผนก A" in html
    r = c.post("/hazards/new", data={"_csrf": csrf(c), **env.form(cf_temp="85.5"), "photos": (io.BytesIO(png()), "a.png")},
               content_type="multipart/form-data")
    assert r.status_code == 302
    hid = int(r.headers["Location"].split("/")[-1])
    h = env.row(hid)
    assert (h["status"], json.loads(h["custom"]), h["reported_by"]) == ("reported", {"temp": 85.5}, c.uid)
    att = env.db.execute("SELECT * FROM hazard_attachments WHERE hazard_id=?", (hid,)).fetchall()
    assert [(a["kind"], a["orig_name"]) for a in att] == [("before", "a.png")]
    assert os.path.isfile(os.path.join(env.app.config["UPLOAD_ROOT"], str(hid), att[0]["stored_name"]))


def test_report_validation_errors_rerender_form_with_input(env):
    c = login(env.app, "reporter")
    r = c.post("/hazards/new", data={"_csrf": csrf(c), **env.form(title="", description="ข้อความที่พิมพ์")})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "กรุณากรอกหัวข้อ" in html and "ข้อความที่พิมพ์" in html
    assert env.db.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 0


def test_reporter_sees_only_own_hazards_assessor_sees_scope(env):
    r1, r2, asr = login(env.app, "reporter"), login(env.app, "reporter"), login(env.app, "assessor")
    env.report(r1, title="ของผู้แจ้งหนึ่ง"); env.report(r2, title="ของผู้แจ้งสอง")
    one, two, both = (c.get("/hazards").get_data(as_text=True) for c in (r1, r2, asr))
    assert "ของผู้แจ้งหนึ่ง" in one and "ของผู้แจ้งสอง" not in one
    assert "ของผู้แจ้งสอง" in two and "ของผู้แจ้งหนึ่ง" not in two
    assert "ของผู้แจ้งหนึ่ง" in both and "ของผู้แจ้งสอง" in both
    scoped = login(env.app, "assessor", factory_id=env.b["f"])
    assert "ของผู้แจ้งหนึ่ง" not in scoped.get("/hazards").get_data(as_text=True)


def test_scoped_user_cannot_open_other_factory_hazard_page_photo_or_post(env):
    rep = login(env.app, "reporter")
    hid = env.report(rep)
    from safety.images import save_attachment
    aid = save_attachment(env.db, env.app.config["UPLOAD_ROOT"], hid, "before", "a.png", png(), rep.uid)
    scoped = login(env.app, "assessor", factory_id=env.b["f"])
    assert scoped.get(f"/hazards/{hid}").status_code == 404
    assert scoped.get(f"/hazards/{hid}/files/{aid}").status_code == 404
    for action, data in (("assess", {"likelihood": "3", "severity": "3"}), ("assign", {"owner_id": "1"}),
                         ("close", {"note": "x"}), ("reject", {"reason": "x"}), ("photos", {"kind": "before"})):
        assert env.act(scoped, hid, action, **data).status_code == 404, action
    assert env.row(hid)["status"] == "reported"


def test_other_reporters_hazard_is_404(env):
    owner, other = login(env.app, "reporter"), login(env.app, "reporter")
    hid = env.report(owner)
    from safety.images import save_attachment
    aid = save_attachment(env.db, env.app.config["UPLOAD_ROOT"], hid, "before", "a.png", png(), owner.uid)
    assert other.get(f"/hazards/{hid}").status_code == 404
    assert other.get(f"/hazards/{hid}/files/{aid}").status_code == 404
    assert env.act(other, hid, "photos", kind="before").status_code == 404
    assert owner.get(f"/hazards/{hid}").status_code == 200 and owner.get(f"/hazards/{hid}/files/{aid}").status_code == 200
    assert owner.get("/hazards/999999").status_code == 404


def action_forms(html):
    return set(re.findall(r'action="/hazards/\d+/(assess|assign|close|reject|photos)"', html))


def test_detail_shows_only_allowed_actions_per_status(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    owner = make_user(env.app, "assessor", name="เจ้าของงาน")
    hid = env.report(rep)
    assert action_forms(rep.get(f"/hazards/{hid}").get_data(as_text=True)) == {"photos"}
    assert action_forms(asr.get(f"/hazards/{hid}").get_data(as_text=True)) == {"assess", "reject", "photos"}
    env.act(asr, hid, "assess", likelihood="3", severity="3")
    assert action_forms(asr.get(f"/hazards/{hid}").get_data(as_text=True)) == {"assess", "assign", "reject", "photos"}
    env.act(asr, hid, "assign", owner_id=str(owner), due_date="")
    assert action_forms(asr.get(f"/hazards/{hid}").get_data(as_text=True)) == {"assign", "close", "reject", "photos"}
    env.act(asr, hid, "close", note="เรียบร้อย")
    assert action_forms(asr.get(f"/hazards/{hid}").get_data(as_text=True)) == set()
    assert action_forms(rep.get(f"/hazards/{hid}").get_data(as_text=True)) == set()


def test_full_flow_via_http(env):
    rep, asr, closer = login(env.app, "reporter"), login(env.app, "assessor"), login(env.app, "dept_head")
    hid = env.report(rep, title="สายไฟชำรุด")
    assert env.act(asr, hid, "assess", likelihood="5", severity="5", note="อันตรายมาก").status_code == 302
    assert env.act(asr, hid, "assign", owner_id=str(closer.uid), due_date="").status_code == 302
    h = env.row(hid)
    assert (h["status"], h["level_name"], h["due_date"]) == ("assigned", "สูงมาก", str(TODAY + timedelta(days=3)))
    assert env.act(closer, hid, "close", note="ถึงเจ้าของงาน").status_code == 302          # เจ้าของงานปิดเองระดับสูงมากไม่ได้
    assert env.row(hid)["status"] == "assigned"
    assert env.act(asr, hid, "close", note="เปลี่ยนสายแล้ว ตรวจผ่าน").status_code == 302
    html = asr.get(f"/hazards/{hid}").get_data(as_text=True)
    assert env.row(hid)["status"] == "closed" and "เปลี่ยนสายแล้ว ตรวจผ่าน" in html and "สูงมาก" in html
    order = [html.index(k) for k in ("แจ้งอันตราย", "ประเมินความเสี่ยง", "มอบหมาย", "ปิดรายการ")]
    assert order == sorted(order)


def test_bad_action_input_flashes_and_changes_nothing(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    hid = env.report(rep)
    for data in ({"likelihood": "abc", "severity": "3"}, {"likelihood": "9", "severity": "3"}, {"likelihood": "", "severity": ""}):
        r = env.act(asr, hid, "assess", **data)
        assert r.status_code == 302 and env.row(hid)["status"] == "reported"
    html = asr.get(f"/hazards/{hid}").get_data(as_text=True)
    assert "ค่าโอกาส" in html or "ต้องเป็น" in html or "อยู่นอกเกณฑ์" in html


def test_reject_via_http(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    hid = env.report(rep)
    env.act(asr, hid, "reject", reason="")
    assert env.row(hid)["status"] == "reported"
    env.act(asr, hid, "reject", reason="รายการซ้ำ")
    assert (env.row(hid)["status"], env.row(hid)["reject_reason"]) == ("rejected", "รายการซ้ำ")
    assert "รายการซ้ำ" in rep.get(f"/hazards/{hid}").get_data(as_text=True)


def test_list_filters_search_sort_whitelist_and_page_clamp(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    ids = [env.report(rep, title=f"หัวข้อ{i:02d}") for i in range(25)]
    env.act(asr, ids[0], "assess", likelihood="5", severity="5")
    env.act(asr, ids[1], "assess", likelihood="1", severity="1")
    env.act(asr, ids[2], "reject", reason="x")
    g = lambda qs: asr.get("/hazards?" + qs)
    assert all(g(qs).status_code == 200 for qs in ("page=0", "page=-5", "page=9999", "page=abc", "sort=title;DROP TABLE hazards",
                                                    "sort=score&dir=sideways", "status=bogus", "level=ไม่มี", "area=abc", "q=%25", "q=_"))
    assert env.db.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 25
    only = g("status=assessed").get_data(as_text=True)
    assert "หัวข้อ00" in only and "หัวข้อ01" in only and "หัวข้อ03" not in only
    assert "หัวข้อ00" in g("level=สูงมาก").get_data(as_text=True) and "หัวข้อ01" not in g("level=สูงมาก").get_data(as_text=True)
    unassessed = g("level=none").get_data(as_text=True)
    assert "หัวข้อ05" in unassessed and "หัวข้อ00" not in unassessed
    assert "หัวข้อ07" in g("q=ข้อ07").get_data(as_text=True) and "หัวข้อ08" not in g("q=ข้อ07").get_data(as_text=True)
    p1, p2 = g("sort=code&dir=asc&page=1").get_data(as_text=True), g("sort=code&dir=asc&page=2").get_data(as_text=True)
    assert "หัวข้อ19" in p1 and "หัวข้อ20" not in p1 and "หัวข้อ20" in p2 and "หัวข้อ00" not in p2
    assert "หัวข้อ24" in g("page=9999&sort=code&dir=asc").get_data(as_text=True)
    mine = rep.get("/hazards?mine=1&sort=code&dir=asc").get_data(as_text=True)
    assert "หัวข้อ00" in mine
    assert "หัวข้อ00" not in asr.get("/hazards?mine=1&sort=code&dir=asc").get_data(as_text=True)
    newest_first = rep.get("/hazards").get_data(as_text=True)                       # ค่าเริ่มต้น: แจ้งล่าสุดก่อน
    assert "หัวข้อ24" in newest_first and "หัวข้อ00" not in newest_first


def test_xss_in_title_description_custom_value_and_filename_escaped(env):
    env.db.execute("INSERT INTO custom_fields(hazard_type_id,key,label_th,type) VALUES(?,?,?,?)", (env.t, "note", "<i>ป้าย</i>", "text"))
    env.db.commit()
    rep = login(env.app, "reporter", name="<b>ชื่อ</b>")
    bad = "<script>alert(1)</script>"
    r = rep.post("/hazards/new", data={"_csrf": csrf(rep), **env.form(title=bad, description=bad, cf_note=bad),
                                       "photos": (io.BytesIO(png()), '"><img src=x onerror=alert(1)>.png')}, content_type="multipart/form-data")
    hid = int(r.headers["Location"].split("/")[-1])
    for url in (f"/hazards/{hid}", "/hazards", "/hazards/new"):
        html = rep.get(url).get_data(as_text=True)
        assert "<script>alert(1)" not in html and "<img src=x" not in html and "<i>ป้าย" not in html and "<b>ชื่อ" not in html, url
    assert "&lt;script&gt;" in rep.get(f"/hazards/{hid}").get_data(as_text=True)


def test_photo_route_serves_jpeg_only_for_visible_hazard_and_matching_id(env):
    from safety.images import save_attachment
    rep = login(env.app, "reporter")
    h1, h2 = env.report(rep), env.report(rep)
    a1 = save_attachment(env.db, env.app.config["UPLOAD_ROOT"], h1, "before", "a.png", png(), rep.uid)
    r = rep.get(f"/hazards/{h1}/files/{a1}")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.get_data()[:3] == b"\xff\xd8\xff"
    assert rep.get(f"/hazards/{h2}/files/{a1}").status_code == 404                       # ไฟล์ของรายการอื่น
    assert rep.get(f"/hazards/{h1}/files/999").status_code == 404
    assert env.app.test_client().get(f"/hazards/{h1}/files/{a1}").status_code == 302


def test_upload_rejects_fake_image_with_message_and_no_row(env):
    rep = login(env.app, "reporter")
    r = rep.post("/hazards/new", data={"_csrf": csrf(rep), **env.form(), "photos": (io.BytesIO(b"not an image"), "evil.jpg")},
                 content_type="multipart/form-data")
    assert r.status_code == 200 and "evil.jpg" in r.get_data(as_text=True) and "รูปภาพ" in r.get_data(as_text=True)
    assert env.db.execute("SELECT COUNT(*) FROM hazards").fetchone()[0] == 0
    hid = env.report(rep)
    r = rep.post(f"/hazards/{hid}/photos", data={"_csrf": csrf(rep), "kind": "before", "photos": (io.BytesIO(b"<svg/>"), "x.png")},
                 content_type="multipart/form-data")
    assert r.status_code == 302 and env.db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 0
    assert "x.png" in rep.get(f"/hazards/{hid}").get_data(as_text=True)                  # ข้อความผิดพลาดแสดงหลัง redirect


def test_add_photos_to_existing_hazard_and_limit(env):
    rep = login(env.app, "reporter")
    hid = env.report(rep)
    files = [(io.BytesIO(png((i * 40, 10, 10))), f"{i}.png") for i in range(5)]
    assert rep.post(f"/hazards/{hid}/photos", data={"_csrf": csrf(rep), "kind": "before", "photos": files},
                    content_type="multipart/form-data").status_code == 302
    assert env.db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 5
    rep.post(f"/hazards/{hid}/photos", data={"_csrf": csrf(rep), "kind": "before", "photos": (io.BytesIO(png()), "6.png")},
             content_type="multipart/form-data")
    assert env.db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 5


def test_after_photo_only_with_close_permission_and_assigned_status(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    hid = env.report(rep)
    up = lambda c, kind: c.post(f"/hazards/{hid}/photos", data={"_csrf": csrf(c), "kind": kind, "photos": (io.BytesIO(png()), "a.png")},
                                content_type="multipart/form-data")
    up(asr, "after")
    up(rep, "after")
    assert env.db.execute("SELECT COUNT(*) FROM hazard_attachments WHERE kind='after'").fetchone()[0] == 0     # ยังไม่ assigned
    env.act(asr, hid, "assess", likelihood="2", severity="2")
    env.act(asr, hid, "assign", owner_id=str(asr.uid), due_date="")
    up(rep, "after")                                                                                          # ผู้แจ้งไม่มีสิทธิ์ปิด
    assert env.db.execute("SELECT COUNT(*) FROM hazard_attachments WHERE kind='after'").fetchone()[0] == 0
    up(asr, "after")
    assert env.db.execute("SELECT COUNT(*) FROM hazard_attachments WHERE kind='after'").fetchone()[0] == 1


def test_close_can_attach_after_photo(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    hid = env.report(rep)
    env.act(asr, hid, "assess", likelihood="2", severity="2"); env.act(asr, hid, "assign", owner_id=str(asr.uid), due_date="")
    r = asr.post(f"/hazards/{hid}/close", data={"_csrf": csrf(asr), "note": "แก้แล้ว", "photos": (io.BytesIO(png()), "after.png")},
                 content_type="multipart/form-data")
    assert r.status_code == 302 and env.row(hid)["status"] == "closed"
    assert [a["kind"] for a in env.db.execute("SELECT kind FROM hazard_attachments")] == ["after"]


def test_overdue_flag_boundary(env):
    rep, asr = login(env.app, "reporter"), login(env.app, "assessor")
    due_today, yesterday, tomorrow, done = (env.report(rep, title=t) for t in ("ครบวันนี้", "เมื่อวาน", "พรุ่งนี้", "ปิดแล้ว"))
    for hid, due, status in ((due_today, TODAY, "assigned"), (yesterday, TODAY - timedelta(days=1), "assigned"),
                             (tomorrow, TODAY + timedelta(days=1), "assigned"), (done, TODAY - timedelta(days=5), "closed")):
        env.db.execute("UPDATE hazards SET status=?, due_date=? WHERE id=?", (status, due.isoformat(), hid))
    env.db.commit()
    html = asr.get("/hazards").get_data(as_text=True)
    rows = {t: re.search(r"<tr[^>]*>(?:(?!</tr>).)*" + t + r"(?:(?!</tr>).)*</tr>", html, re.S).group(0)
            for t in ("ครบวันนี้", "เมื่อวาน", "พรุ่งนี้", "ปิดแล้ว")}
    assert "เกินกำหนด" in rows["เมื่อวาน"]
    assert all("เกินกำหนด" not in rows[t] for t in ("ครบวันนี้", "พรุ่งนี้", "ปิดแล้ว"))


def test_deactivated_custom_field_still_shown_on_old_hazard(env):
    env.db.execute("INSERT INTO custom_fields(hazard_type_id,key,label_th,type) VALUES(?,?,?,?)", (env.t, "temp", "อุณหภูมิเดิม", "number"))
    env.db.commit()
    rep = login(env.app, "reporter")
    hid = env.report(rep, cf_temp="77")
    env.db.execute("UPDATE custom_fields SET active=0"); env.db.commit()
    html = rep.get(f"/hazards/{hid}").get_data(as_text=True)
    assert "อุณหภูมิเดิม" in html and "77" in html
    assert "อุณหภูมิเดิม" not in rep.get("/hazards/new").get_data(as_text=True)


def test_post_without_permission_is_403_never_200(env):
    rep = login(env.app, "reporter")
    hid = env.report(rep)
    for action, data in (("assess", {"likelihood": "3", "severity": "3"}), ("assign", {"owner_id": str(rep.uid)}),
                         ("close", {"note": "x"}), ("reject", {"reason": "x"})):
        assert env.act(rep, hid, action, **data).status_code == 403, action
    assert env.row(hid)["status"] == "reported"
    viewer = login(env.app, "viewer")
    assert viewer.get("/hazards/new").status_code == 403 and viewer.post("/hazards/new", data={"_csrf": csrf(viewer), **env.form()}).status_code == 403
    assert viewer.get(f"/hazards/{hid}").status_code == 200                                # master.view เห็นทั้งหมด แต่ทำอะไรไม่ได้


def test_post_without_csrf_is_400(env):
    rep = login(env.app, "reporter")
    assert rep.post("/hazards/new", data=env.form()).status_code == 400
