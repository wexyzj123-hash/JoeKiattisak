import pytest

from safety.db import connect
from safety.master import REGISTRY, active_options
from tests.conftest import csrf
from tests.safety.helpers import login, sql

SLUGS = ["hazard-types", "hazard-sources"]
TABLE = {"hazard-types": "hazard_types", "hazard-sources": "hazard_sources"}


def create(c, slug, code="HZ-01", name_th="สารเคมีรั่ว", **extra):
    return c.post(f"/master/{slug}/new", data={"_csrf": csrf(c), "code": code, "name_th": name_th, **extra})


def count(app, slug):
    return sql(app, f"SELECT COUNT(*) FROM {TABLE[slug]}")[0][0]


def test_registry_has_both_tables():
    assert {"hazard-types", "hazard-sources"} <= set(REGISTRY)
    assert REGISTRY["hazard-types"].label_th == "ประเภทอันตราย" and REGISTRY["hazard-sources"].label_th == "แหล่งที่มา"


@pytest.mark.parametrize("slug", SLUGS)
def test_list_requires_master_view(safety_app, slug):
    assert login(safety_app, "reporter").get(f"/master/{slug}").status_code == 403
    assert login(safety_app, "viewer").get(f"/master/{slug}").status_code == 200
    assert safety_app.test_client().get(f"/master/{slug}").status_code == 302


@pytest.mark.parametrize("slug", SLUGS)
def test_create_and_list_shows_creator(safety_app, slug):
    c = login(safety_app, name="สมชาย ใจดี")
    assert create(c, slug).status_code == 302
    html = c.get(f"/master/{slug}").get_data(as_text=True)
    assert "HZ-01" in html and "สารเคมีรั่ว" in html and "สมชาย ใจดี" in html and "ใช้งาน" in html


@pytest.mark.parametrize("slug", SLUGS)
def test_code_duplicate_rejected_case_and_whitespace(safety_app, slug):
    c = login(safety_app)
    create(c, slug, code="ab-01")
    for dup in ("AB-01", " ab-01 "):
        assert create(c, slug, code=dup).status_code == 200
    assert count(safety_app, slug) == 1


@pytest.mark.parametrize("code", ["", "a b", "x" * 33, "ก", "a/b", "a;b"])
def test_invalid_code_rejected(safety_app, code):
    c = login(safety_app)
    assert create(c, "hazard-types", code=code).status_code == 200
    assert count(safety_app, "hazard-types") == 0


def test_name_required(safety_app):
    c = login(safety_app)
    assert create(c, "hazard-types", name_th="  ").status_code == 200
    assert count(safety_app, "hazard-types") == 0


def test_edit_updates_and_keeps_code_unique(safety_app):
    c = login(safety_app)
    create(c, "hazard-types", code="A1", name_th="หนึ่ง")
    create(c, "hazard-types", code="B1", name_th="สอง")
    ids = {r["code"]: r["id"] for r in sql(safety_app, "SELECT id, code FROM hazard_types")}
    ed = lambda code, name: c.post(f"/master/hazard-types/{ids['B1']}/edit",
                                   data={"_csrf": csrf(c), "code": code, "name_th": name, "description": "รายละเอียด"})
    assert ed("a1", "สอง").status_code == 200                         # ชนกับ A1
    assert ed("B1", "สองใหม่").status_code == 302                      # ใช้รหัสเดิมของตัวเองได้
    row = sql(safety_app, "SELECT name_th, description FROM hazard_types WHERE code='B1'")[0]
    assert (row["name_th"], row["description"]) == ("สองใหม่", "รายละเอียด")


def test_edit_missing_row_404(safety_app):
    c = login(safety_app)
    assert c.get("/master/hazard-types/999/edit").status_code == 404
    assert c.post("/master/hazard-types/999/toggle", data={"_csrf": csrf(c)}).status_code == 404


def test_toggle_hides_from_active_options_but_row_stays_listed(safety_app):
    c = login(safety_app)
    create(c, "hazard-sources", code="S1", name_th="เครื่องจักร")
    rid = sql(safety_app, "SELECT id FROM hazard_sources")[0][0]
    con = connect(safety_app.config["DATABASE"])
    assert [r["code"] for r in active_options(con, REGISTRY["hazard-sources"])] == ["S1"]
    assert c.post(f"/master/hazard-sources/{rid}/toggle", data={"_csrf": csrf(c)}).status_code == 302
    assert active_options(con, REGISTRY["hazard-sources"]) == []
    html = c.get("/master/hazard-sources").get_data(as_text=True)
    assert "S1" in html and "ปิดใช้งาน" in html
    c.post(f"/master/hazard-sources/{rid}/toggle", data={"_csrf": csrf(c)})
    assert len(active_options(con, REGISTRY["hazard-sources"])) == 1


def test_search_matches_code_and_thai_name(safety_app):
    c = login(safety_app)
    create(c, "hazard-types", code="CHEM", name_th="สารเคมีรั่วไหล")
    create(c, "hazard-types", code="FALL", name_th="ตกจากที่สูง")
    by_name = c.get("/master/hazard-types?q=เคมี").get_data(as_text=True)
    assert "CHEM" in by_name and "FALL" not in by_name
    by_code = c.get("/master/hazard-types?q=fall").get_data(as_text=True)
    assert "FALL" in by_code and "CHEM" not in by_code


def test_search_escapes_like_wildcards(safety_app):
    c = login(safety_app)
    create(c, "hazard-types", code="A_1", name_th="มีขีดล่าง")
    create(c, "hazard-types", code="AB1", name_th="ไม่มี")
    assert "A_1" in c.get("/master/hazard-types?q=_").get_data(as_text=True)
    only = c.get("/master/hazard-types?q=_").get_data(as_text=True)
    assert "AB1" not in only
    assert "AB1" not in c.get("/master/hazard-types?q=%25").get_data(as_text=True)


@pytest.mark.parametrize("qs", ["sort=name_th;DROP TABLE hazard_types", "sort=id", "sort=1;--", "dir=sideways",
                                "sort=code&dir=DESC", "sort=name_th&dir=desc", "sort=creator", "sort=active"])
def test_sort_whitelist_falls_back(safety_app, qs):
    c = login(safety_app)
    create(c, "hazard-types")
    assert c.get(f"/master/hazard-types?{qs}").status_code == 200
    assert count(safety_app, "hazard-types") == 1


def test_sort_direction_applies(safety_app):
    c = login(safety_app)
    for code in ("A1", "B1"):
        create(c, "hazard-types", code=code, name_th=code)
    asc = c.get("/master/hazard-types?sort=code&dir=asc").get_data(as_text=True)
    desc = c.get("/master/hazard-types?sort=code&dir=desc").get_data(as_text=True)
    assert asc.index("A1") < asc.index("B1") and desc.index("B1") < desc.index("A1")


@pytest.mark.parametrize("page", ["0", "-1", "9999", "abc", ""])
def test_page_clamped(safety_app, page):
    c = login(safety_app)
    for i in range(25):
        create(c, "hazard-types", code=f"C{i:02d}", name_th=f"ชื่อ{i}")
    assert c.get(f"/master/hazard-types?page={page}").status_code == 200


def test_pagination_20_per_page(safety_app):
    c = login(safety_app)
    for i in range(25):
        create(c, "hazard-types", code=f"C{i:02d}", name_th=f"ชื่อ{i}")
    p1 = c.get("/master/hazard-types?page=1").get_data(as_text=True)
    p2 = c.get("/master/hazard-types?page=2").get_data(as_text=True)
    assert "C00" in p1 and "C19" in p1 and "C20" not in p1
    assert "C20" in p2 and "C24" in p2 and "C00" not in p2
    assert "C20" in c.get("/master/hazard-types?page=9999").get_data(as_text=True)


def test_script_in_name_is_escaped(safety_app):
    c = login(safety_app)
    create(c, "hazard-types", code="XSS", name_th="<script>alert(1)</script>")
    html = c.get("/master/hazard-types").get_data(as_text=True)
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


def test_thai_combining_characters_searchable(safety_app):
    c = login(safety_app)
    create(c, "hazard-types", code="TH", name_th="ที่สูง น้ำมัน")
    assert "TH" in c.get("/master/hazard-types?q=น้ำมัน").get_data(as_text=True)


@pytest.mark.parametrize("slug", SLUGS)
def test_post_without_master_edit_is_403_even_with_csrf(safety_app, slug):
    c = login(safety_app, "viewer")
    assert create(c, slug).status_code == 403
    assert count(safety_app, slug) == 0
    assert c.get(f"/master/{slug}/new").status_code == 403


def test_unknown_slug_404(safety_app):
    assert login(safety_app).get("/master/nope").status_code == 404
