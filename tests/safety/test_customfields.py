import pytest

from safety.customfields import fields_for_type, validate_custom
from safety.db import connect
from tests.conftest import csrf
from tests.safety.factories import add_refs
from tests.safety.helpers import login, sql


def fld(key, type_, required=0, options="", label=None):
    return {"key": key, "label_th": label or f"ฟิลด์ {key}", "type": type_, "options": options, "required": required}


@pytest.mark.parametrize("type_, options, raw, ok, expected", [
    ("text", "", "สวัสดี", True, "สวัสดี"), ("text", "", "  ตัดช่องว่าง  ", True, "ตัดช่องว่าง"),
    ("text", "", "x" * 500, True, "x" * 500), ("text", "", "x" * 501, False, None),
    ("number", "", "12.5", True, 12.5), ("number", "", "7", True, 7), ("number", "", "-3", True, -3),
    ("number", "", "nan", False, None), ("number", "", "inf", False, None), ("number", "", "-inf", False, None),
    ("number", "", "1e999", False, None), ("number", "", "abc", False, None), ("number", "", "1,5", False, None),
    ("date", "", "2026-03-31", True, "2026-03-31"), ("date", "", "2026-02-30", False, None),
    ("date", "", "31/03/2026", False, None), ("date", "", "20260331", False, None), ("date", "", "2026-3-1", False, None),
    ("select", "ก\nข\nค", "ข", True, "ข"), ("select", "ก\nข\nค", "ง", False, None), ("select", "ก\nข\nค", "ข ", True, "ข"),
    ("checkbox", "", "1", True, True), ("checkbox", "", "on", True, True),
])
def test_validate_each_type_valid_and_invalid(type_, options, raw, ok, expected):
    values, errors = validate_custom([fld("f", type_, options=options)], {"cf_f": raw})
    assert (errors == []) is ok, errors
    if ok:
        assert values == {"f": expected} and type(values["f"]) is type(expected)


def test_optional_empty_omitted_and_checkbox_defaults_false():
    values, errors = validate_custom([fld("a", "text"), fld("b", "number"), fld("c", "checkbox")], {})
    assert errors == [] and values == {"c": False}


def test_required_missing_reported_with_label():
    fields = [fld("a", "text", 1, label="ผู้ควบคุม"), fld("b", "date", 1, label="วันที่พบ"), fld("c", "checkbox", 1, label="รับทราบ")]
    values, errors = validate_custom(fields, {"cf_a": "  "})
    assert len(errors) == 3 and any("ผู้ควบคุม" in e for e in errors) and any("รับทราบ" in e for e in errors)
    assert validate_custom(fields, {"cf_a": "x", "cf_b": "2026-01-01", "cf_c": "1"})[1] == []


def test_unknown_keys_ignored_and_script_text_kept_raw():
    values, errors = validate_custom([fld("a", "text")], {"cf_a": "<script>1</script>", "cf_zzz": "x", "a": "no-prefix"})
    assert errors == [] and values == {"a": "<script>1</script>"}                  # หน้าจอ escape เอง ไม่ดัดแปลงข้อมูล


def test_fields_for_type_includes_global_and_excludes_inactive(safety_app):
    c = connect(safety_app.config["DATABASE"])
    t, _ = add_refs(c)
    t2 = c.execute("INSERT INTO hazard_types(code,name_th) VALUES('T2','อื่น')").lastrowid
    ins = lambda tid, key, sort=0, active=1: c.execute(
        "INSERT INTO custom_fields(hazard_type_id,key,label_th,type,sort,active) VALUES(?,?,?,?,?,?)",
        (tid, key, key, "text", sort, active))
    ins(None, "glob", 2); ins(t, "mine", 1); ins(t2, "other", 0); ins(t, "off", 3, 0)
    c.commit()
    assert [f["key"] for f in fields_for_type(c, t)] == ["mine", "glob"]
    assert [f["key"] for f in fields_for_type(c, t, include_inactive=True)] == ["mine", "glob", "off"]
    assert [f["key"] for f in fields_for_type(c, None)] == ["glob"]
    assert [f["key"] for f in fields_for_type(c, t2)] == ["other", "glob"]


def post(c, **data):
    return c.post("/admin/custom-fields", data={"_csrf": csrf(c), **data})


def create(c, **kw):
    return post(c, action="create", **{"key": "temp", "label_th": "อุณหภูมิ", "type": "number", "hazard_type_id": "", **kw})


def test_page_requires_criteria_manage(safety_app):
    assert login(safety_app, "viewer").get("/admin/custom-fields").status_code == 403
    assert post(login(safety_app, "assessor"), action="create").status_code == 403
    assert safety_app.test_client().get("/admin/custom-fields").status_code == 302
    assert login(safety_app).get("/admin/custom-fields").status_code == 200


def test_admin_create_edit_toggle(safety_app):
    c = login(safety_app)
    t, _ = add_refs(connect(safety_app.config["DATABASE"]))
    assert create(c, hazard_type_id=str(t), required="1", sort="3").status_code == 302
    row = sql(safety_app, "SELECT * FROM custom_fields")[0]
    assert (row["key"], row["type"], row["hazard_type_id"], row["required"], row["sort"], row["active"]) == \
        ("temp", "number", t, 1, 3, 1)
    post(c, action="update", id=str(row["id"]), label_th="อุณหภูมิ °C", required="", sort="9", options="")
    row = sql(safety_app, "SELECT * FROM custom_fields")[0]
    assert (row["label_th"], row["required"], row["sort"]) == ("อุณหภูมิ °C", 0, 9)
    post(c, action="toggle", id=str(row["id"]))
    assert sql(safety_app, "SELECT active FROM custom_fields")[0][0] == 0
    assert "อุณหภูมิ °C" in c.get("/admin/custom-fields").get_data(as_text=True)


def test_key_type_and_hazard_type_immutable_after_create(safety_app):
    c = login(safety_app)
    t, _ = add_refs(connect(safety_app.config["DATABASE"]))
    create(c, hazard_type_id=str(t))
    fid = sql(safety_app, "SELECT id FROM custom_fields")[0][0]
    post(c, action="update", id=str(fid), key="changed", type="text", hazard_type_id="", label_th="ใหม่", options="", sort="0")
    row = sql(safety_app, "SELECT * FROM custom_fields")[0]
    assert (row["key"], row["type"], row["hazard_type_id"], row["label_th"]) == ("temp", "number", t, "ใหม่")


@pytest.mark.parametrize("bad", [{"key": "A"}, {"key": "1ab"}, {"key": "a b"}, {"key": "x" * 33}, {"key": ""},
                                 {"label_th": "  "}, {"label_th": "x" * 101}, {"type": "money"}, {"hazard_type_id": "999"},
                                 {"hazard_type_id": "abc"}])
def test_bad_create_rejected(safety_app, bad):
    assert create(login(safety_app), **bad).status_code in (200, 302)
    assert sql(safety_app, "SELECT 1 FROM custom_fields") == []


def test_duplicate_key_same_type_rejected_but_allowed_on_other_type(safety_app):
    c = login(safety_app)
    con = connect(safety_app.config["DATABASE"])
    t, _ = add_refs(con)
    t2 = con.execute("INSERT INTO hazard_types(code,name_th) VALUES('T2','อื่น')").lastrowid
    con.commit()
    create(c, hazard_type_id=str(t)); create(c, hazard_type_id=str(t))
    assert len(sql(safety_app, "SELECT 1 FROM custom_fields")) == 1
    create(c, hazard_type_id=str(t2)); create(c, hazard_type_id="")
    assert len(sql(safety_app, "SELECT 1 FROM custom_fields")) == 3
    create(c, hazard_type_id="")                                         # ซ้ำกับ "ทุกประเภท" ที่มีอยู่
    assert len(sql(safety_app, "SELECT 1 FROM custom_fields")) == 3


def test_select_needs_two_distinct_options(safety_app):
    c = login(safety_app)
    create(c, key="one", type="select", options="เดียว")
    create(c, key="dup", type="select", options="ก\nก")
    create(c, key="blank", type="select", options="\n \n")
    assert sql(safety_app, "SELECT 1 FROM custom_fields") == []
    create(c, key="ok", type="select", options="ก\r\nข\n  ค  ")
    assert sql(safety_app, "SELECT options FROM custom_fields")[0][0] == "ก\nข\nค"


def test_script_in_label_and_options_escaped(safety_app):
    c = login(safety_app)
    create(c, key="xss", label_th="<script>alert(1)</script>", type="select", options="<b>x</b>\ny")
    html = c.get("/admin/custom-fields").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html and "<b>x</b>" not in html
