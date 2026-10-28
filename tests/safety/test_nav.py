import re

import pytest

from safety.nav import nav_items
from safety.perms import PERMISSIONS
from tests.safety.helpers import login

ALL = set(PERMISSIONS)


def labels(groups):
    return [[i.label for i in g.items] for g in groups]


HOME = ["หน้าหลัก"]


def test_admin_sees_home_plus_all_groups_in_screenshot_order():
    groups = nav_items(ALL, "/")
    assert [g.title for g in groups] == ["เมนูหลัก", "ระบบข้อมูลหลัก", "อุบัติเหตุ", "ตรวจความปลอดภัย & CAPA", "ข้อมูลองค์กร", "ผู้ดูแลระบบ"]
    assert labels(groups) == [
        HOME,
        ["ภาพรวมดำเนินงาน", "ภาพรวมตามหน่วยงาน", "พื้นที่เสี่ยง", "ลงทะเบียนบันทึกอันตราย", "แหล่งที่มา", "ประเภทอันตราย"],
        ["รายงานอุบัติเหตุ / Near Miss", "สถิติอุบัติเหตุ"],
        ["ตรวจความปลอดภัย (Safety Patrol)", "CAPA"],
        ["โรงงาน", "แผนก", "ไลน์/กลุ่มงาน", "สาเหตุอุบัติเหตุ"],
        ["ประเภทผู้ใช้งาน", "ผู้ใช้งาน", "เกณฑ์ความเสี่ยง", "ฟิลด์เพิ่มเติม", "ระดับความรุนแรงการบาดเจ็บ", "แบบตรวจความปลอดภัย", "แผนการตรวจ", "ตั้งค่า CAPA", "การแจ้งเตือน"]]
    hrefs = [i.href for g in groups for i in g.items]
    assert hrefs == ["/", "/overview", "/overview/departments", "/master/risk-areas", "/hazards", "/master/hazard-sources",
                     "/master/hazard-types", "/incidents", "/incidents/stats", "/patrols", "/capas", "/master/factories", "/master/departments", "/master/lines",
                     "/master/incident-causes", "/admin/roles", "/admin/users", "/admin/criteria", "/admin/custom-fields",
                     "/admin/injury-severities", "/admin/checklists", "/admin/patrol-plans", "/admin/capa-settings", "/admin/notifications"]


def test_nav_items_hide_unpermitted():
    viewer = nav_items({"master.view"}, "/")
    assert [g.title for g in viewer] == ["เมนูหลัก", "ระบบข้อมูลหลัก", "ข้อมูลองค์กร"]
    assert "ลงทะเบียนบันทึกอันตราย" not in labels(viewer)[1]
    assert labels(nav_items({"hazard.report"}, "/")) == [HOME, ["ลงทะเบียนบันทึกอันตราย"], ["CAPA"]]                # เจ้าของ CAPA ทั่วไปเข้าหน้า CAPA ได้
    assert labels(nav_items({"incident.report"}, "/")) == [HOME, ["รายงานอุบัติเหตุ / Near Miss"], ["CAPA"]]
    assert labels(nav_items({"patrol.view"}, "/")) == [HOME, ["ตรวจความปลอดภัย (Safety Patrol)"]]
    assert labels(nav_items({"capa.verify"}, "/")) == [HOME, ["CAPA"]]
    assert nav_items(set(), "/") == []
    assert labels(nav_items({"users.manage"}, "/")) == [HOME, ["ผู้ใช้งาน", "การแจ้งเตือน"]]
    assert labels(nav_items({"criteria.manage"}, "/")) == [HOME, ["เกณฑ์ความเสี่ยง", "ฟิลด์เพิ่มเติม", "ระดับความรุนแรงการบาดเจ็บ", "ตั้งค่า CAPA"]]
    assert labels(nav_items({"roles.manage"}, "/")) == [HOME, ["ประเภทผู้ใช้งาน"]]


@pytest.mark.parametrize("path, active", [
    ("/overview", "ภาพรวมดำเนินงาน"),
    ("/overview/departments", "ภาพรวมตามหน่วยงาน"),                  # ไม่ให้ /overview ติดไปด้วย
    ("/master/hazard-types", "ประเภทอันตราย"),
    ("/master/hazard-types/5/edit", "ประเภทอันตราย"),
    ("/master/risk-areas/new", "พื้นที่เสี่ยง"),
    ("/admin/users", "ผู้ใช้งาน"),
    ("/admin/criteria", "เกณฑ์ความเสี่ยง"),
    ("/admin/custom-fields", "ฟิลด์เพิ่มเติม"),
    ("/admin/notifications", "การแจ้งเตือน"),
    ("/hazards/12", "ลงทะเบียนบันทึกอันตราย"),
])
def test_nav_marks_exactly_one_active_item(path, active):
    marked = [i.label for g in nav_items(ALL, path) for i in g.items if i.active]
    assert marked == [active]


def test_nav_marks_home_active_only_on_root():
    assert [i.label for g in nav_items(ALL, "/") for i in g.items if i.active] == HOME
    assert [i.label for g in nav_items(ALL, "/nowhere") for i in g.items if i.active] == []


def test_every_nav_item_has_a_known_icon():
    from shared.icons import ICONS
    for g in nav_items(ALL, "/"):
        for i in g.items:
            assert i.icon in ICONS, (i.label, i.icon)


def test_rendered_sidebar_for_admin_and_viewer(safety_app):
    html = login(safety_app, "admin").get("/master/factories").get_data(as_text=True)
    for text in ("ระบบข้อมูลหลัก", "ข้อมูลองค์กร", "ผู้ดูแลระบบ", "ประเภทผู้ใช้งาน", "ลงทะเบียนบันทึกอันตราย"):
        assert text in html
    assert 'class="nav-item active"' in html
    viewer = login(safety_app, "viewer").get("/master/factories").get_data(as_text=True)
    assert "ประเภทผู้ใช้งาน" not in viewer and "ผู้ดูแลระบบ" not in viewer and "ข้อมูลองค์กร" in viewer


def test_shell_shows_brand_tagline_footer_and_wordmark(safety_app):
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    for text in ("Safety 4.0", "Safe People · Safe Workplace · Sustainable Future", "Safety Today, Better Tomorrow"):
        assert text in html
    assert 'class="wordmark"' in html


def test_top_bar_has_search_bell_toggle_and_user_menu(safety_app):
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    assert 'class="topbar"' in html and 'action="/search"' in html and 'name="q"' in html
    assert 'aria-label="ค้นหารายการอันตราย"' in html and 'data-theme-toggle' in html and 'id="bell-badge"' in html
    assert '<details class="user-menu">' in html and 'href="/profile"' in html
    assert re.search(r"<summary aria-label=\"เมนูผู้ใช้", html)
    assert "ผู้ดูแลระบบ" in html[html.index('class="menu-head"'):html.index('class="menu-head"') + 300]       # ชื่อบทบาทภาษาไทย ไม่ใช่รหัส
    assert 'action="/logout"' in html and 'name="_csrf"' in html


def test_icon_only_controls_are_labelled(safety_app):
    import re
    html = login(safety_app, "admin").get("/").get_data(as_text=True)
    for tag in re.findall(r"<(?:button|a|summary)\b[^>]*>(?:(?!</(?:button|a|summary)>).)*</(?:button|a|summary)>", html, re.S):
        text = re.sub(r"<svg.*?</svg>|<[^>]+>", "", tag, flags=re.S).strip()
        assert text or "aria-label" in tag.split(">")[0], tag[:120]


def test_reporter_sees_only_hazard_registration_in_nav(safety_app):
    html = login(safety_app, "reporter").get("/hazards").get_data(as_text=True)
    assert "ลงทะเบียนบันทึกอันตราย" in html and "ข้อมูลองค์กร" not in html and "ประเภทอันตราย" not in html


def test_home_and_admin_pages_use_the_shell(safety_app):
    c = login(safety_app)
    for path in ("/", "/admin/roles", "/admin/users", "/master/hazard-types"):
        html = c.get(path).get_data(as_text=True)
        assert 'class="sidebar"' in html, path


def test_sso_error_page_has_no_sidebar(safety_app):
    html = safety_app.test_client().get("/sso?token=bad").get_data(as_text=True)
    assert 'class="sidebar"' not in html


def test_notifications_admin_page_200_for_user_managers_only(safety_app):
    page = login(safety_app, "admin").get("/admin/notifications")
    assert page.status_code == 200 and "กฎการแจ้งเตือน" in page.get_data(as_text=True)
    assert safety_app.test_client().get("/admin/notifications").status_code == 302
    assert login(safety_app, "viewer").get("/admin/notifications").status_code == 403


def test_every_nav_link_resolves_for_admin(safety_app):
    c = login(safety_app, "admin")
    for g in nav_items(ALL, "/"):
        for item in g.items:
            assert c.get(item.href).status_code == 200, item.href
