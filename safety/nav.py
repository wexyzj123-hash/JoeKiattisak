"""safety/nav.py — เมนูด้านข้างตามภาพตัวอย่าง: 3 กลุ่ม, ซ่อนรายการที่ไม่มีสิทธิ์, ไฮไลต์หน้าปัจจุบัน"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    label: str
    href: str
    active: bool = False


@dataclass
class Group:
    title: str
    items: list[Item]


# (กลุ่ม, [(ชื่อเมนู, ลิงก์, สิทธิ์ที่ต้องมี)])
MENU = (
    ("ระบบข้อมูลหลัก", (
        ("ภาพรวมดำเนินงาน", "/overview", "master.view"),
        ("ภาพรวมตามหน่วยงาน", "/overview/departments", "master.view"),
        ("พื้นที่เสี่ยง", "/master/risk-areas", "master.view"),
        ("ลงทะเบียนบันทึกอันตราย", "/hazards", "hazard.report"),
        ("แหล่งที่มา", "/master/hazard-sources", "master.view"),
        ("ประเภทอันตราย", "/master/hazard-types", "master.view"),
    )),
    ("ข้อมูลองค์กร", (
        ("โรงงาน", "/master/factories", "master.view"),
        ("แผนก", "/master/departments", "master.view"),
        ("ไลน์/กลุ่มงาน", "/master/lines", "master.view"),
    )),
    ("ผู้ดูแลระบบ", (
        ("ประเภทผู้ใช้งาน", "/admin/roles", "roles.manage"),
        ("ผู้ใช้งาน", "/admin/users", "users.manage"),
    )),
)


def nav_items(user_permissions: set[str], current_path: str) -> list[Group]:
    groups = [Group(title, [Item(label, href) for label, href, perm in items if perm in user_permissions])
              for title, items in MENU]
    groups = [g for g in groups if g.items]
    matches = [i for g in groups for i in g.items
               if current_path == i.href or current_path.startswith(i.href + "/")]
    if matches:
        max(matches, key=lambda i: len(i.href)).active = True        # ตรงที่สุดเพียงรายการเดียว
    return groups
