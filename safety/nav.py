"""safety/nav.py — เมนูด้านข้างตามภาพตัวอย่าง: 3 กลุ่ม, ซ่อนรายการที่ไม่มีสิทธิ์, ไฮไลต์หน้าปัจจุบัน"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    label: str
    href: str
    icon: str = "file"
    active: bool = False


@dataclass
class Group:
    title: str
    items: list[Item]


# (กลุ่ม, [(ชื่อเมนู, ลิงก์, ไอคอน, สิทธิ์ที่ต้องมี)])
MENU = (
    ("ระบบข้อมูลหลัก", (
        ("ภาพรวมดำเนินงาน", "/overview", "overview", "master.view"),
        ("ภาพรวมตามหน่วยงาน", "/overview/departments", "departments", "master.view"),
        ("พื้นที่เสี่ยง", "/master/risk-areas", "map-pin", "master.view"),
        ("ลงทะเบียนบันทึกอันตราย", "/hazards", "clipboard", "hazard.report"),
        ("แหล่งที่มา", "/master/hazard-sources", "source", "master.view"),
        ("ประเภทอันตราย", "/master/hazard-types", "tag", "master.view"),
    )),
    ("อุบัติเหตุ", (
        ("รายงานอุบัติเหตุ / Near Miss", "/incidents", "alert", "incident.report"),
        ("สถิติอุบัติเหตุ", "/incidents/stats", "overview", "incident.investigate"),
    )),
    ("ตรวจความปลอดภัย & CAPA", (
        ("ตรวจความปลอดภัย (Safety Patrol)", "/patrols", "clipboard", "patrol.perform|patrol.view"),
        ("CAPA", "/capas", "check", "capa.create|capa.verify|hazard.report|incident.report|patrol.perform"),
    )),
    ("รายงาน & สถิติ", (
        ("รายงานและสถิติ", "/reports", "overview", "report.view"),
    )),
    ("ข้อมูลองค์กร", (
        ("โรงงาน", "/master/factories", "building", "master.view"),
        ("แผนก", "/master/departments", "layers", "master.view"),
        ("ไลน์/กลุ่มงาน", "/master/lines", "sliders", "master.view"),
        ("สาเหตุอุบัติเหตุ", "/master/incident-causes", "tag", "master.view"),
    )),
    ("ผู้ดูแลระบบ", (
        ("ประเภทผู้ใช้งาน", "/admin/roles", "shield", "roles.manage"),
        ("ผู้ใช้งาน", "/admin/users", "users", "users.manage"),
        ("เกณฑ์ความเสี่ยง", "/admin/criteria", "alert", "criteria.manage"),
        ("ฟิลด์เพิ่มเติม", "/admin/custom-fields", "puzzle", "criteria.manage"),
        ("ระดับความรุนแรงการบาดเจ็บ", "/admin/injury-severities", "sliders", "criteria.manage"),
        ("แบบตรวจความปลอดภัย", "/admin/checklists", "clipboard", "patrol.manage"),
        ("แผนการตรวจ", "/admin/patrol-plans", "clock", "patrol.manage"),
        ("ตั้งค่า CAPA", "/admin/capa-settings", "sliders", "criteria.manage"),
        ("การแจ้งเตือน", "/admin/notifications", "bell", "users.manage"),
    )),
)


def nav_items(user_permissions: set[str], current_path: str) -> list[Group]:
    groups = [Group(title, [Item(label, href, icon) for label, href, icon, perm in items if any(p in user_permissions for p in perm.split("|"))])
              for title, items in MENU]
    groups = [g for g in groups if g.items]
    if groups:                                                                   # หน้าหลักปักไว้บนสุดสำหรับทุกคนที่เห็นเมนูใด ๆ
        groups.insert(0, Group("เมนูหลัก", [Item("หน้าหลัก", "/", "home")]))
    matches = [i for g in groups for i in g.items
               if current_path == i.href or (i.href != "/" and current_path.startswith(i.href + "/"))]
    if matches:
        max(matches, key=lambda i: len(i.href)).active = True        # ตรงที่สุดเพียงรายการเดียว
    return groups
