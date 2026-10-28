import pytest

from safety.nav import nav_items
from safety.perms import PERMISSIONS
from tests.safety.helpers import login

ALL = set(PERMISSIONS)


def labels(groups):
    return [[i.label for i in g.items] for g in groups]


def test_admin_sees_all_three_groups_in_screenshot_order():
    groups = nav_items(ALL, "/")
    assert [g.title for g in groups] == ["ระบบข้อมูลหลัก", "ข้อมูลองค์กร", "ผู้ดูแลระบบ"]
    assert labels(groups) == [
        ["ภาพรวมดำเนินงาน", "ภาพรวมตามหน่วยงาน", "พื้นที่เสี่ยง", "ลงทะเบียนบันทึกอันตราย", "แหล่งที่มา", "ประเภทอันตราย"],
        ["โรงงาน", "แผนก", "ไลน์/กลุ่มงาน"],
        ["ประเภทผู้ใช้งาน", "ผู้ใช้งาน"]]
    hrefs = [i.href for g in groups for i in g.items]
    assert hrefs == ["/overview", "/overview/departments", "/master/risk-areas", "/hazards", "/master/hazard-sources",
                     "/master/hazard-types", "/master/factories", "/master/departments", "/master/lines",
                     "/admin/roles", "/admin/users"]


def test_nav_items_hide_unpermitted():
    viewer = nav_items({"master.view"}, "/")
    assert [g.title for g in viewer] == ["ระบบข้อมูลหลัก", "ข้อมูลองค์กร"]
    assert "ลงทะเบียนบันทึกอันตราย" not in labels(viewer)[0]
    assert labels(nav_items({"hazard.report"}, "/")) == [["ลงทะเบียนบันทึกอันตราย"]]
    assert nav_items(set(), "/") == []
    only_users = nav_items({"users.manage"}, "/")
    assert labels(only_users) == [["ผู้ใช้งาน"]]


@pytest.mark.parametrize("path, active", [
    ("/overview", "ภาพรวมดำเนินงาน"),
    ("/overview/departments", "ภาพรวมตามหน่วยงาน"),                  # ไม่ให้ /overview ติดไปด้วย
    ("/master/hazard-types", "ประเภทอันตราย"),
    ("/master/hazard-types/5/edit", "ประเภทอันตราย"),
    ("/master/risk-areas/new", "พื้นที่เสี่ยง"),
    ("/admin/users", "ผู้ใช้งาน"),
])
def test_nav_marks_exactly_one_active_item(path, active):
    marked = [i.label for g in nav_items(ALL, path) for i in g.items if i.active]
    assert marked == [active]


def test_nav_marks_nothing_on_other_pages():
    assert [i for g in nav_items(ALL, "/") for i in g.items if i.active] == []


def test_rendered_sidebar_for_admin_and_viewer(safety_app):
    html = login(safety_app, "admin").get("/master/factories").get_data(as_text=True)
    for text in ("ระบบข้อมูลหลัก", "ข้อมูลองค์กร", "ผู้ดูแลระบบ", "ประเภทผู้ใช้งาน", "ลงทะเบียนบันทึกอันตราย"):
        assert text in html
    assert 'class="nav-item active"' in html
    viewer = login(safety_app, "viewer").get("/master/factories").get_data(as_text=True)
    assert "ประเภทผู้ใช้งาน" not in viewer and "ผู้ดูแลระบบ" not in viewer and "ข้อมูลองค์กร" in viewer


def test_reporter_sees_only_hazard_registration_in_nav(safety_app):
    html = login(safety_app, "reporter").get("/hazards").get_data(as_text=True)
    assert "ลงทะเบียนบันทึกอันตราย" in html and "ข้อมูลองค์กร" not in html and "ประเภทอันตราย" not in html


@pytest.mark.parametrize("path", ["/overview", "/overview/departments", "/hazards"])
def test_placeholder_pages_200_for_logged_in_and_redirect_for_anonymous(safety_app, path):
    page = login(safety_app, "viewer").get(path)
    assert page.status_code == 200 and "เฟส 3" in page.get_data(as_text=True)
    assert safety_app.test_client().get(path).status_code == 302


def test_home_and_admin_pages_use_the_shell(safety_app):
    c = login(safety_app)
    for path in ("/", "/admin/roles", "/admin/users", "/master/hazard-types"):
        html = c.get(path).get_data(as_text=True)
        assert 'class="sidebar"' in html, path


def test_sso_error_page_has_no_sidebar(safety_app):
    html = safety_app.test_client().get("/sso?token=bad").get_data(as_text=True)
    assert 'class="sidebar"' not in html
