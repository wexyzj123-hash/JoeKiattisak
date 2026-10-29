import pytest

from safety.db import connect
from safety.risk import apply_criteria, criteria_form_values, list_levels, max_score, scale_size
from tests.conftest import csrf
from tests.safety.factories import add_chain, add_hazard, add_user
from tests.safety.helpers import login, sql


@pytest.fixture
def con(safety_app):
    c = connect(safety_app.config["DATABASE"])
    yield c
    c.close()


def form(con, **changes):
    f = criteria_form_values(con)
    f.update({k: str(v) for k, v in changes.items()})
    return f


def snapshot(con):
    return (con.execute("SELECT kind, level, label_th, description FROM risk_scales ORDER BY kind, level").fetchall(),
            [tuple(l) for l in list_levels(con)])


def post(c, data):
    return c.post("/admin/criteria", data={"_csrf": csrf(c), **data})


def test_page_requires_criteria_manage(safety_app):
    assert login(safety_app, "viewer").get("/admin/criteria").status_code == 403
    assert login(safety_app, "assessor").get("/admin/criteria").status_code == 403
    assert safety_app.test_client().get("/admin/criteria").status_code == 302
    html = login(safety_app).get("/admin/criteria").get_data(as_text=True)
    assert "แทบไม่เกิด" in html and "สูงมาก" in html and "#ff4d4f" in html


def test_form_values_roundtrip_is_a_noop(con):
    before = snapshot(con)
    assert apply_criteria(con, criteria_form_values(con)) == []
    assert snapshot(con) == before


def test_save_labels_and_levels_ok(safety_app, con):
    f = form(con, likelihood_label_1="แทบเป็นไปไม่ได้", severity_desc_2="บาดเจ็บเล็กน้อย",
             level_name_0="เบา", level_color_0="#00FF00", level_sla_0=45, level_approval_0="1")
    assert post(login(safety_app), f).status_code == 302
    lv = list_levels(connect(safety_app.config["DATABASE"]))[0]
    assert (lv["name_th"], lv["color"], lv["sla_days"], lv["requires_approval"]) == ("เบา", "#00FF00", 45, 1)
    assert sql(safety_app, "SELECT label_th FROM risk_scales WHERE kind='likelihood' AND level=1")[0][0] == "แทบเป็นไปไม่ได้"
    assert sql(safety_app, "SELECT description FROM risk_scales WHERE kind='severity' AND level=2")[0][0] == "บาดเจ็บเล็กน้อย"


def test_grow_scale_requires_levels_to_cover_new_max(con):
    before = snapshot(con)
    errors = apply_criteria(con, form(con, likelihood_size=6, likelihood_label_6="เกิดแน่นอน"))
    assert errors and any("26-30" in e for e in errors)
    assert snapshot(con) == before and scale_size(con, "likelihood") == 5


def test_grow_scale_with_extended_levels_ok(con):
    last = len(list_levels(con)) - 1
    assert apply_criteria(con, form(con, likelihood_size=6, likelihood_label_6="เกิดแน่นอน", **{f"level_max_{last}": 30})) == []
    assert scale_size(con, "likelihood") == 6 and max_score(con) == 30


def test_grow_scale_needs_a_label_for_the_new_level(con):
    last = len(list_levels(con)) - 1
    errors = apply_criteria(con, form(con, likelihood_size=6, **{f"level_max_{last}": 30}))
    assert errors and scale_size(con, "likelihood") == 5


def test_gap_and_overlap_rejected_atomically(con):
    before = snapshot(con)
    assert apply_criteria(con, form(con, level_min_1=6, likelihood_label_1="เปลี่ยน"))        # ช่องว่าง 5
    assert snapshot(con) == before
    assert apply_criteria(con, form(con, level_min_1=4, level_name_0="เปลี่ยนชื่อ"))            # ซ้อน 4
    assert snapshot(con) == before


def test_shrink_below_used_value_rejected_and_allowed_when_unused(con):
    ch, u = add_chain(con), add_user(con)
    hid = add_hazard(con, ch["a"], u, likelihood=5, severity=2, score=10, status="assessed")
    last = len(list_levels(con)) - 1
    shrink = form(con, likelihood_size=4, **{f"level_max_{last}": 20})
    errors = apply_criteria(con, shrink)
    assert errors and any("รายการอันตราย" in e and "ค่า 5" in e for e in errors) and scale_size(con, "likelihood") == 5
    con.execute("UPDATE hazards SET likelihood=4 WHERE id=?", (hid,))
    con.commit()
    assert apply_criteria(con, shrink) == [] and scale_size(con, "likelihood") == 4
    assert con.execute("SELECT COUNT(*) FROM risk_scales WHERE kind='likelihood'").fetchone()[0] == 4


@pytest.mark.parametrize("changes", [
    {"likelihood_size": 1}, {"severity_size": 11}, {"likelihood_size": "abc"}, {"severity_size": ""},
    {"level_color_0": "red"}, {"level_sla_0": -1}, {"level_sla_0": "x"}, {"level_name_0": "  "},
    {"likelihood_label_1": "  "}, {"severity_label_3": "x" * 101}, {"level_min_0": "abc"}, {"level_count": "abc"},
])
def test_bad_inputs_rejected_with_no_change(con, changes):
    before = snapshot(con)
    assert apply_criteria(con, form(con, **changes))
    assert snapshot(con) == before


def test_blank_extra_rows_ignored_and_delete_merges(con):
    n = len(list_levels(con))
    f = form(con)
    assert int(f["level_count"]) >= n + 1                                    # มีแถวว่างให้เพิ่มระดับ
    assert apply_criteria(con, f) == [] and len(list_levels(con)) == n


def test_criteria_edit_does_not_change_existing_hazard_snapshot_and_keeps_link(con):
    ch, u = add_chain(con), add_user(con)
    lid = list_levels(con)[0]["id"]
    hid = add_hazard(con, ch["a"], u, likelihood=1, severity=3, score=3, risk_level_id=lid, level_name="ต่ำ",
                     level_color="#2ecc71", status="assessed")
    assert apply_criteria(con, form(con, level_name_0="เบา", level_color_0="#0000FF")) == []
    h = con.execute("SELECT * FROM hazards WHERE id=?", (hid,)).fetchone()
    assert (h["level_name"], h["level_color"], h["score"], h["risk_level_id"]) == ("ต่ำ", "#2ecc71", 3, lid)
    assert con.execute("SELECT name_th FROM risk_levels WHERE id=?", (lid,)).fetchone()[0] == "เบา"


def test_deleting_a_referenced_level_unlinks_but_keeps_snapshot(con):
    ch, u = add_chain(con), add_user(con)
    lid = list_levels(con)[0]["id"]
    hid = add_hazard(con, ch["a"], u, score=3, risk_level_id=lid, level_name="ต่ำ", level_color="#2ecc71", status="assessed")
    assert apply_criteria(con, form(con, level_delete_0=1, level_min_1=1)) == []
    h = con.execute("SELECT * FROM hazards WHERE id=?", (hid,)).fetchone()
    assert h["risk_level_id"] is None and h["level_name"] == "ต่ำ" and len(list_levels(con)) == 3


def test_swapping_level_names_works(con):
    assert apply_criteria(con, form(con, level_name_0="ปานกลาง", level_name_1="ต่ำ")) == []
    assert [l["name_th"] for l in list_levels(con)][:2] == ["ปานกลาง", "ต่ำ"]


def test_duplicate_level_names_rejected(con):
    assert apply_criteria(con, form(con, level_name_1="ต่ำ"))


def test_script_in_label_escaped(safety_app, con):
    post(login(safety_app), form(con, likelihood_label_1="<script>alert(1)</script>"))
    html = login(safety_app).get("/admin/criteria").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_rejected_post_rerenders_with_errors_and_keeps_input(safety_app, con):
    r = post(login(safety_app), form(con, level_color_0="red", likelihood_label_1="ข้อความที่พิมพ์ไว้"))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "#RRGGBB" in html and "ข้อความที่พิมพ์ไว้" in html
