"""safety/views/pages.py — หน้าที่เปิดใช้งานในเฟสถัดไป (แสดงตัวยึดตำแหน่งไว้ก่อน)"""
from __future__ import annotations

from flask import Blueprint, render_template

from ..security import safety_login_required

bp = Blueprint("pages", __name__)


def _soon(title: str):
    return render_template("coming_soon.html", title=title)


@bp.route("/overview")
@safety_login_required
def overview():
    return _soon("ภาพรวมดำเนินงาน")


@bp.route("/overview/departments")
@safety_login_required
def overview_departments():
    return _soon("ภาพรวมตามหน่วยงาน")
