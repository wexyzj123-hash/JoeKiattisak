"""safety/perms.py — คีย์สิทธิ์ของ Safety (ตัวตรวจสิทธิ์เพิ่มใน Task 2)"""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "master.view": "ดูข้อมูลหลัก",
    "master.edit": "แก้ไขข้อมูลหลัก",
    "users.manage": "จัดการผู้ใช้งาน",
    "roles.manage": "จัดการประเภทผู้ใช้งาน",
    "hazard.report": "แจ้ง/บันทึกอันตราย",
    "hazard.assess": "ประเมินความเสี่ยง",
    "hazard.assign": "มอบหมายผู้รับผิดชอบ",
    "hazard.close": "ปิดรายการอันตราย",
    "criteria.manage": "จัดการเกณฑ์ความเสี่ยง",
}
