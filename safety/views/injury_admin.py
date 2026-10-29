"""safety/views/injury_admin.py — แอดมินปรับระดับความรุนแรงของการบาดเจ็บ (เปลี่ยนชื่อ/ลำดับ/นับเป็นอุบัติเหตุหยุดงาน/ปิดใช้งาน — ไม่มีการลบ)"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request

from ..db import get_db
from ..perms import permission_required

bp = Blueprint("injury_admin", __name__, url_prefix="/admin")
NAME_MAX = 200


def _int(raw):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


@bp.route("/injury-severities", methods=["GET", "POST"])
@permission_required("criteria.manage")
def injury_severities():
    db = get_db()
    if request.method == "POST":
        f = request.form
        row = db.execute("SELECT * FROM injury_severities WHERE id=?", (_int(f.get("id")),)).fetchone()
        name, rank = f.get("name_th", "").strip(), _int(f.get("rank"))
        if f.get("action") not in (None, "", "update"):
            flash("ไม่รองรับการดำเนินการนี้ (ระดับความรุนแรงลบไม่ได้ ใช้การปิดใช้งาน)")
        elif row is None:
            flash("ไม่พบระดับความรุนแรง")
        elif not name or len(name) > NAME_MAX:
            flash(f"กรุณากรอกชื่อ (ไม่เกิน {NAME_MAX} ตัวอักษร)")
        elif rank is None or not 0 <= rank <= 99:
            flash("ลำดับต้องเป็นจำนวนเต็ม 0-99")
        else:
            db.execute("UPDATE injury_severities SET name_th=?, rank=?, counts_lost_time=?, active=? WHERE id=?",
                       (name, rank, 1 if f.get("counts_lost_time") not in (None, "", "0") else 0,
                        1 if f.get("active") not in (None, "", "0") else 0, row["id"]))
            db.commit()
        return redirect("/admin/injury-severities")
    rows = db.execute("SELECT * FROM injury_severities ORDER BY rank, id").fetchall()
    return render_template("injury_admin.html", rows=rows)
