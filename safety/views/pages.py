"""safety/views/pages.py — หน้าที่เปิดใช้งานในเฟสถัดไป (แสดงตัวยึดตำแหน่งไว้ก่อน)"""
from __future__ import annotations

from flask import Blueprint, render_template

from ..perms import permission_required

bp = Blueprint("pages", __name__)


def _soon(title: str, phase: int):
    return render_template("coming_soon.html", title=title, phase=phase)


@bp.route("/admin/notifications")
@permission_required("users.manage")
def notifications():
    return _soon("การแจ้งเตือน", 4)
