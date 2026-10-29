"""ปัญหาเล็กน้อยที่เลื่อนไว้ของเฟส 7: ผู้รับสำรองรอบตรวจแก้ได้ในกฎ · หน้า CAPA เคารพสิทธิ์ต้นทางและรายชื่อเจ้าของ · ช่องถ่ายรูปเลือกจากแกลเลอรีได้"""
import re
from datetime import date, datetime

from safety import scheduler
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_user
from tests.safety.helpers import login, sql
from tests.safety.test_patrol_notify import W, db  # noqa: F401
from tests.safety.test_capa import fixed  # noqa: F401


def test_patrol_fallback_role_is_a_rule_setting_the_admin_can_change(db, fixed):
    w = W(db)
    w.plan()                                                                                       # ไม่มีผู้ตรวจประจำแผน
    db.execute("UPDATE notify_rules SET recipients='owner_else:dept_head' WHERE event='patrol_due'"); db.commit()
    scheduler.run_daily(db, today=date(2026, 3, 1), force=True, now=datetime(2026, 3, 1, 8, 0))
    got = {n["user_id"] for n in w.notes("patrol_due")}
    assert w.head in got and w.asr_a not in got


def test_legacy_patrol_rules_are_migrated_once_and_later_edits_stay(tmp_path):
    con = connect(str(tmp_path / "m.db")); ensure_schema(con)
    con.execute("DELETE FROM app_settings WHERE key='patrol_fallback_migrated'")
    con.execute("UPDATE notify_rules SET recipients='owner,role:dept_head' WHERE event='patrol_overdue' AND offset_days=1"); con.commit()
    ensure_schema(con)
    rec = {r[0] for r in con.execute("SELECT recipients FROM notify_rules WHERE event='patrol_overdue'")}
    assert rec == {"owner_else:assessor,role:dept_head"}
    con.execute("UPDATE notify_rules SET recipients='owner' WHERE event='patrol_due'"); con.commit()
    ensure_schema(con)
    assert con.execute("SELECT recipients FROM notify_rules WHERE event='patrol_due'").fetchone()[0] == "owner"


def test_admin_form_accepts_owner_else_only_with_a_real_role(safety_app):
    from tests.conftest import csrf
    c = login(safety_app, "admin")
    tok = csrf(c)
    form = {"action": "rule_create", "event": "patrol_due", "channels": ["inapp"], "offset_days": "0", "_csrf": tok}
    c.post("/admin/notifications", data={**form, "recipients": "owner_else:assessor"})
    c.post("/admin/notifications", data={**form, "recipients": "owner_else:nosuchrole"})
    got = [r[0] for r in sql(safety_app, "SELECT recipients FROM notify_rules WHERE event='patrol_due' ORDER BY id")]
    assert "owner_else:assessor" in got and "owner_else:nosuchrole" not in got


def test_capa_detail_hides_source_the_viewer_cannot_open_and_owner_list_is_scoped(safety_app):
    con = connect(safety_app.config["DATABASE"])
    a, b = add_chain(con, "A"), add_chain(con, "B")
    rep = add_user(con, "reporter", "ผู้แจ้งอื่น")
    hid = add_hazard(con, a["a"], rep, code="HZ-SECRET-1", title="หัวข้อลับ")
    con.commit(); con.close()
    c = login(safety_app, "reporter", factory_id=a["f"])
    con = connect(safety_app.config["DATABASE"])
    outsider = add_user(con, "assessor", "ผู้ประเมินโรงงาน B", factory_id=b["f"])
    cid = con.execute("INSERT INTO capas(code, source_kind, source_id, action_type, title, risk_area_id, owner_id, due_date, created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                      ("CP-M-1", "hazard", hid, "corrective", "งานแก้ไข", a["a"], c.uid, "2026-12-01", rep)).lastrowid
    con.commit(); con.close()
    html = c.get(f"/capas/{cid}").get_data(as_text=True)
    assert "งานแก้ไข" in html and "HZ-SECRET-1" not in html and "หัวข้อลับ" not in html
    assert "ผู้ประเมินโรงงาน B" not in html
    admin = login(safety_app, "admin").get(f"/capas/{cid}").get_data(as_text=True)
    assert "HZ-SECRET-1" in admin


def test_photo_inputs_allow_choosing_from_gallery(safety_app):
    c = login(safety_app, "admin")
    import pathlib
    for t in ("patrol_round", "hazard_form", "incident_form"):
        assert "capture=" not in (pathlib.Path("safety/templates") / f"{t}.html").read_text(encoding="utf-8")
