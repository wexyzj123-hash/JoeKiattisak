import pytest

from safety.db import connect
from safety.master import REGISTRY, ParentDef, active_options, breadcrumb
from tests.conftest import csrf
from tests.safety.helpers import login, sql


def post_new(c, slug, **form):
    return c.post(f"/master/{slug}/new", data={"_csrf": csrf(c), **form})


def one(app, table, code):
    return sql(app, f"SELECT id FROM {table} WHERE code=?", (code,))[0][0]


def build_chain(app, c):
    post_new(c, "factories", code="F1", name_th="โรงงาน 1")
    post_new(c, "departments", code="D1", name_th="ปั๊มขึ้นรูป", factory_id=one(app, "factories", "F1"))
    post_new(c, "lines", code="L1", name_th="ไลน์ A", department_id=one(app, "departments", "D1"))
    post_new(c, "risk-areas", code="R1", name_th="จุดป้อนวัตถุดิบ", line_id=one(app, "lines", "L1"))


def test_registry_has_org_tables_with_parents():
    assert REGISTRY["factories"].parent is None
    assert REGISTRY["departments"].parent == ParentDef("factory_id", "factories", "โรงงาน")
    assert REGISTRY["lines"].parent == ParentDef("department_id", "departments", "แผนก")
    assert REGISTRY["risk-areas"].parent == ParentDef("line_id", "lines", "ไลน์/กลุ่มงาน")
    assert [REGISTRY[s].label_th for s in ("factories", "departments", "lines", "risk-areas")] == \
        ["โรงงาน", "แผนก", "ไลน์/กลุ่มงาน", "พื้นที่เสี่ยง"]


def test_full_chain_create_and_breadcrumb(safety_app):
    c = login(safety_app)
    build_chain(safety_app, c)
    con = connect(safety_app.config["DATABASE"])
    assert breadcrumb(con, REGISTRY["risk-areas"], one(safety_app, "risk_areas", "R1")) == \
        "โรงงาน 1 › ปั๊มขึ้นรูป › ไลน์ A › จุดป้อนวัตถุดิบ"
    assert breadcrumb(con, REGISTRY["factories"], one(safety_app, "factories", "F1")) == "โรงงาน 1"


@pytest.mark.parametrize("parent", [None, "999", "abc", ""])
def test_child_requires_existing_parent(safety_app, parent):
    c = login(safety_app)
    form = {"code": "D1", "name_th": "แผนก"}
    if parent is not None:
        form["factory_id"] = parent
    assert post_new(c, "departments", **form).status_code == 200
    assert sql(safety_app, "SELECT 1 FROM departments") == []


def test_child_under_inactive_parent_rejected(safety_app):
    c = login(safety_app)
    post_new(c, "factories", code="F1", name_th="โรงงาน 1")
    fid = one(safety_app, "factories", "F1")
    c.post(f"/master/factories/{fid}/toggle", data={"_csrf": csrf(c)})
    assert post_new(c, "departments", code="D1", name_th="x", factory_id=fid).status_code == 200
    assert sql(safety_app, "SELECT 1 FROM departments") == []


def test_inactive_parent_not_in_selector_but_children_still_listed(safety_app):
    c = login(safety_app)
    build_chain(safety_app, c)
    post_new(c, "factories", code="F2", name_th="โรงงาน 2")
    c.post(f"/master/factories/{one(safety_app, 'factories', 'F1')}/toggle", data={"_csrf": csrf(c)})
    form = c.get("/master/departments/new").get_data(as_text=True)
    assert "โรงงาน 2" in form and "โรงงาน 1" not in form
    listed = c.get("/master/departments").get_data(as_text=True)
    assert "ปั๊มขึ้นรูป" in listed and "โรงงาน 1" in listed          # แถวลูกยังอยู่ และแสดงชื่อแม่
    con = connect(safety_app.config["DATABASE"])
    assert [r["code"] for r in active_options(con, REGISTRY["factories"])] == ["F2"]


def test_edit_child_with_inactive_unchanged_parent_allowed_but_changing_to_inactive_is_not(safety_app):
    c = login(safety_app)
    build_chain(safety_app, c)
    post_new(c, "factories", code="F2", name_th="โรงงาน 2")
    f1, f2, d1 = (one(safety_app, "factories", "F1"), one(safety_app, "factories", "F2"),
                  one(safety_app, "departments", "D1"))
    c.post(f"/master/factories/{f1}/toggle", data={"_csrf": csrf(c)})
    c.post(f"/master/factories/{f2}/toggle", data={"_csrf": csrf(c)})
    ed = lambda fid: c.post(f"/master/departments/{d1}/edit", data={
        "_csrf": csrf(c), "code": "D1", "name_th": "ชื่อใหม่", "factory_id": fid})
    assert ed(f1).status_code == 302                                     # แม่เดิม (ปิดอยู่) แก้ชื่อลูกได้
    assert ed(f2).status_code == 200                                     # ย้ายไปแม่ที่ปิดอยู่ไม่ได้
    assert sql(safety_app, "SELECT factory_id, name_th FROM departments")[0][:] == (f1, "ชื่อใหม่")


def test_codes_unique_per_table_not_global(safety_app):
    c = login(safety_app)
    post_new(c, "factories", code="A1", name_th="โรงงาน")
    fid = one(safety_app, "factories", "A1")
    assert post_new(c, "departments", code="A1", name_th="แผนก", factory_id=fid).status_code == 302
    assert post_new(c, "departments", code="a1", name_th="แผนกซ้ำ", factory_id=fid).status_code == 200


def test_parent_column_shown_and_sortable(safety_app):
    c = login(safety_app)
    build_chain(safety_app, c)
    html = c.get("/master/lines?sort=parent&dir=desc").get_data(as_text=True)
    assert "ปั๊มขึ้นรูป" in html and "แผนก" in html
    assert c.get("/master/factories?sort=parent").status_code == 200       # ไม่มีแม่ → ถอยไปเรียงตามรหัส


def test_form_shows_parent_select_only_for_child_tables(safety_app):
    c = login(safety_app)
    assert 'name="factory_id"' in c.get("/master/departments/new").get_data(as_text=True)
    assert "factory_id" not in c.get("/master/factories/new").get_data(as_text=True)
