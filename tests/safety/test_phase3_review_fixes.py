"""การแก้ไขตามผลทบทวนเฟส 3 — แต่ละข้อต้องล้มก่อนแก้ (RED) แล้วผ่านหลังแก้"""
import io
import json
import sqlite3
from datetime import date

import pytest
from PIL import Image

from safety import timeutil, workflow
from safety.app import create_app
from safety.db import connect
from safety.images import ImageError, process_image
from safety.risk import apply_criteria, criteria_form_values, list_levels
from safety.schema import ensure_schema
from tests.conftest import SECRET, csrf
from tests.safety.factories import add_chain, add_hazard, add_refs, add_user, user_row
from tests.safety.helpers import login
from tests.safety.test_workflow import W, TODAY  # noqa: F401  (ใช้ fixture w ผ่าน import ด้านล่าง)
from tests.safety.test_workflow import w, fixed_today  # noqa: F401


# ---------- C1: เพดานขนาดคำขอและคีย์ลับต้องมีผลจริง ----------
def test_upload_limit_is_applied_by_default_and_413_is_a_thai_page(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "s.db"), "SSO_SECRET": SECRET, "SERVICE_TOKEN": "x",
                      "HUB_URL": "http://hub.test", "UPLOAD_ROOT": str(tmp_path / "up")})
    assert app.config["MAX_CONTENT_LENGTH"] == 25 * 1024 * 1024
    c = login(app, "reporter")
    big = io.BytesIO(b"\xff" * (26 * 1024 * 1024))
    r = c.post("/hazards/new", data={"_csrf": csrf(c), "title": "x", "photos": (big, "big.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 413 and "25 MB" in r.get_data(as_text=True) and "Request Entity" not in r.get_data(as_text=True)


def test_secret_keys_from_environment_are_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFETY_SECRET_KEY", "safety-key-from-env")
    monkeypatch.setenv("HUB_SECRET_KEY", "hub-key-from-env")
    from hub.app import create_app as hub_create
    s = create_app({"DATABASE": str(tmp_path / "s.db"), "SSO_SECRET": SECRET, "SERVICE_TOKEN": "x", "HUB_URL": "http://h"})
    h = hub_create({"DATABASE": str(tmp_path / "h.db"), "SSO_SECRET": SECRET, "SERVICE_TOKEN": "x", "SAFETY_URL": "http://s"})
    assert s.config["SECRET_KEY"] == "safety-key-from-env" and h.config["SECRET_KEY"] == "hub-key-from-env"


# ---------- I1: ผู้รับผิดชอบเดิมห้ามปิดรายการระดับต้องอนุมัติ แม้โอนงานออกแล้ว ----------
def test_owner_cannot_reassign_away_then_close_a_four_eyes_hazard(w):
    head = add_user(w.db, "dept_head", "หัวหน้า 1")
    head2 = add_user(w.db, "dept_head", "หัวหน้า 2")
    hid = w.assessed(5, 5)
    assert workflow.assign(w.db, w.u("asr"), hid, head, "") == []
    assert workflow.assign(w.db, user_row(w.db, head), hid, head2, "") == []            # โอนงานต่อให้หัวหน้า 2
    assert any("เคยเป็นผู้รับผิดชอบ" in e or "ผู้รับผิดชอบ" in e for e in workflow.close(w.db, user_row(w.db, head), hid, "ปิดเอง"))
    assert w.row(hid)["status"] == "assigned"
    assert workflow.close(w.db, user_row(w.db, head2), hid, "ตรวจแล้ว")                    # head2 เป็นเจ้าของปัจจุบัน → ปิดเองไม่ได้
    assert w.row(hid)["status"] == "assigned"
    assert workflow.close(w.db, w.u("asr"), hid, "ผู้ประเมินปิดแทน") == []              # คนที่ไม่เคยเป็นเจ้าของปิดได้


def test_non_four_eyes_level_unaffected(w):
    head = add_user(w.db, "dept_head", "หัวหน้า")
    hid = w.assessed(1, 1)
    workflow.assign(w.db, w.u("asr"), hid, head, "")
    assert workflow.close(w.db, user_row(w.db, head), hid, "แก้เอง") == []


# ---------- I2: ตรวจแข่งกันแม้สถานะเดิม (ประเมินซ้ำ/โอนงาน) ----------
def _race(monkeypatch, w, hid, do_other):
    real = workflow.visible_hazard
    state = {"fired": False}

    def racing(db, user, hazard_id):
        row = real(db, user, hazard_id)
        if not state["fired"]:
            state["fired"] = True
            do_other()
        return row
    monkeypatch.setattr(workflow, "visible_hazard", racing)


def test_stale_reassess_loses_and_audit_stays_truthful(w, monkeypatch):
    hid = w.assessed(1, 1)
    _race(monkeypatch, w, hid, lambda: workflow.assess(w.db, w.u("asr2"), hid, 5, 5))
    assert workflow.assess(w.db, w.u("asr"), hid, 2, 2)                                   # ต้องแพ้
    h = w.row(hid)
    assert h["score"] == 25
    assessed = [json.loads(e["data"]) for e in w.db.execute("SELECT data FROM hazard_events WHERE kind='assessed' ORDER BY id")]
    assert [a["new"]["score"] for a in assessed] == [1, 25] and assessed[1]["old"]["score"] == 1


def test_four_eyes_race_via_reassign_to_closer_fails(w, monkeypatch):
    closer = w.ids["asr"]
    hid = w.assigned(5, 5, owner="asr2")
    _race(monkeypatch, w, hid, lambda: workflow.assign(w.db, w.u("asr2"), hid, closer, ""))
    assert workflow.close(w.db, w.u("asr"), hid, "ปิด")                                    # อ่านตอนเจ้าของเป็น asr2 แต่ถูกโอนมาที่ผู้ปิดระหว่างทาง
    assert w.row(hid)["status"] == "assigned"


def test_version_increments_on_every_transition(w):
    hid = w.hazard()
    v0 = w.row(hid)["version"]
    workflow.assess(w.db, w.u("asr"), hid, 2, 2)
    workflow.assess(w.db, w.u("asr"), hid, 3, 3)
    assert w.row(hid)["version"] == v0 + 2


# ---------- I3: ผู้รับผิดชอบต้องมองเห็นและอยู่ในขอบเขตของรายการ ----------
def test_owner_must_be_in_hazard_scope(w):
    hid = w.assessed()
    scoped_b = add_user(w.db, "assessor", "นอกขอบเขต", factory_id=w.b["f"])
    assert workflow.assign(w.db, w.u("asr"), hid, scoped_b, "") and w.row(hid)["status"] == "assessed"
    scoped_a = add_user(w.db, "assessor", "ในขอบเขต", factory_id=w.a["f"])
    assert workflow.assign(w.db, w.u("asr"), hid, scoped_a, "") == []


def test_reporter_only_owner_can_see_and_list_the_hazard(w):
    from safety import stats
    from safety.hazards import visible_hazard
    hid = w.assessed()
    other = add_user(w.db, "reporter", "ผู้แจ้งอื่น")
    assert visible_hazard(w.db, user_row(w.db, other), hid) is None
    assert workflow.assign(w.db, w.u("asr"), hid, other, "") == []
    assert visible_hazard(w.db, user_row(w.db, other), hid) is not None
    assert stats.counts_by_status(w.db, user_row(w.db, other))["assigned"] == 1


def test_owner_sees_hazard_page_and_mine_list_over_http(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    t, s = add_refs(con)
    rep = login(safety_app, "reporter")
    owner = login(safety_app, "reporter")
    hid = add_hazard(con, ch["a"], rep.uid, status="assigned", owner_id=owner.uid, title="งานของเจ้าของ")
    assert owner.get(f"/hazards/{hid}").status_code == 200
    assert "งานของเจ้าของ" in owner.get("/hazards?mine=1").get_data(as_text=True)
    assert login(safety_app, "reporter").get(f"/hazards/{hid}").status_code == 404


# ---------- I4: ฟิลด์เพิ่มเติมคีย์ซ้ำข้ามประเภท ----------
def test_global_and_typed_field_cannot_share_a_key(safety_app):
    c = login(safety_app)
    con = connect(safety_app.config["DATABASE"])
    t, _ = add_refs(con)
    mk = lambda tid, key="loc": c.post("/admin/custom-fields", data={"_csrf": csrf(c), "action": "create", "hazard_type_id": tid,
                                                                      "key": key, "label_th": "ที่ตั้ง", "type": "text"})
    mk(str(t)); mk("")                                                                      # global ชนกับ typed
    assert len(con.execute("SELECT 1 FROM custom_fields").fetchall()) == 1
    mk("", "other"); mk(str(t), "other")                                                    # typed ชนกับ global
    assert len(con.execute("SELECT 1 FROM custom_fields").fetchall()) == 2


def test_form_tolerates_hidden_duplicate_inputs_and_disables_hidden_ones(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ch = add_chain(con)
    t, s = add_refs(con)
    t2 = con.execute("INSERT INTO hazard_types(code,name_th) VALUES('T2','อื่น')").lastrowid
    con.executemany("INSERT INTO custom_fields(hazard_type_id,key,label_th,type,options,required) VALUES(?,?,?,?,?,?)",
                    [(t, "loc", "ที่ตั้ง A", "select", "a\nb", 0), (t2, "loc", "ที่ตั้ง B", "select", "p\nq", 1)])
    con.commit()
    rep = login(safety_app, "reporter")
    # เบราว์เซอร์ที่ไม่มี JS ส่งช่องของประเภท A (ว่าง) มาก่อนช่องของประเภท B — เซิร์ฟเวอร์ต้องไม่ถือว่าว่าง
    from werkzeug.datastructures import MultiDict
    form = MultiDict([("_csrf", csrf(rep)), ("title", "x"), ("risk_area_id", str(ch["a"])), ("hazard_type_id", str(t2)),
                      ("source_id", str(s)), ("cf_loc", ""), ("cf_loc", "p")])
    r = rep.post("/hazards/new", data=form)
    assert r.status_code == 302
    html = rep.get("/hazards/new").get_data(as_text=True)
    assert "disabled" in html and "querySelectorAll('input, select, textarea')" in html          # JS ปิดช่องที่ซ่อน


# ---------- I5: level_id ซ้ำในฟอร์มเดียว ----------
def test_duplicate_level_id_in_one_submit_rejected(safety_app):
    con = connect(safety_app.config["DATABASE"])
    ensure_schema(con)
    before = [tuple(l) for l in list_levels(con)]
    f = criteria_form_values(con)
    f["level_id_1"] = f["level_id_0"]
    errors = apply_criteria(con, f)
    assert errors and any("ซ้ำ" in e for e in errors)
    assert [tuple(l) for l in list_levels(con)] == before


# ---------- I6: JPEG หลายเฟรม (MPO) จากโทรศัพท์ ----------
def test_multi_frame_mpo_accepted_using_primary_frame_only():
    buf = io.BytesIO()
    first, second = Image.new("RGB", (300, 200), (200, 10, 10)), Image.new("RGB", (60, 40), (10, 10, 200))
    first.save(buf, "MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(buf.getvalue())).format == "MPO" and getattr(Image.open(io.BytesIO(buf.getvalue())), "is_animated", False)
    out = Image.open(io.BytesIO(process_image(buf.getvalue())))
    assert out.format == "JPEG" and out.size == (300, 200) and out.getpixel((5, 5))[0] > 150


def test_animated_webp_and_gif_still_rejected():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(ImageError):
        process_image(buf.getvalue())
    g = io.BytesIO(); frames[0].save(g, "GIF")
    with pytest.raises(ImageError):
        process_image(g.getvalue())


# ---------- I7: INSERT OR REPLACE ต้องแก้ประวัติไม่ได้ ----------
def test_events_cannot_be_overwritten_with_insert_or_replace(tmp_path):
    db = connect(str(tmp_path / "s.db")); ensure_schema(db)
    ch, u = add_chain(db), add_user(db)
    hid = add_hazard(db, ch["a"], u)
    from safety.hazards import add_event
    add_event(db, hid, u, "reported", None, "reported", note="เดิม"); db.commit()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("INSERT OR REPLACE INTO hazard_events(id, hazard_id, kind, note) VALUES(1, ?, 'x', 'TAMPERED')", (hid,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("REPLACE INTO hazard_events(id, hazard_id, kind, note) VALUES(1, ?, 'x', 'TAMPERED')", (hid,))
    assert db.execute("SELECT note, kind FROM hazard_events").fetchone()[:] == ("เดิม", "reported")
    db.execute("INSERT INTO hazard_events(hazard_id, kind) VALUES(?, 'ok')", (hid,))          # เพิ่มแถวใหม่ปกติยังทำได้
