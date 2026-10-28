"""safety/views/custom_fields.py — จัดการฟิลด์เพิ่มเติมของแต่ละประเภทอันตราย"""
from __future__ import annotations

import re
import sqlite3

from flask import Blueprint, flash, redirect, render_template, request

from ..customfields import TYPES, split_options
from ..db import get_db
from ..perms import permission_required

bp = Blueprint("custom_fields", __name__, url_prefix="/admin")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def _int(raw):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _options_or_error(kind: str, raw: str):
    if kind != "select":
        return "", None
    opts = split_options(raw)
    if len(opts) < 2 or len(set(opts)) != len(opts) or any(len(o) > 100 for o in opts):
        return "", "ตัวเลือกต้องมีอย่างน้อย 2 ข้อ ไม่ซ้ำกัน และแต่ละข้อไม่เกิน 100 ตัวอักษร"
    return "\n".join(opts), None


@bp.route("/custom-fields", methods=["GET", "POST"])
@permission_required("criteria.manage")
def custom_fields():
    db = get_db()
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        if action == "create":
            key, label, kind = f.get("key", "").strip(), f.get("label_th", "").strip(), f.get("type", "")
            raw_type = f.get("hazard_type_id", "").strip()
            type_id = _int(raw_type) if raw_type else None
            options, opt_err = _options_or_error(kind, f.get("options", ""))
            if not KEY_RE.match(key):
                flash("รหัสฟิลด์ต้องขึ้นต้นด้วย a-z ใช้ a-z 0-9 _ ยาว 2-32 ตัว")
            elif not label or len(label) > 100:
                flash("กรุณากรอกชื่อฟิลด์ (ไม่เกิน 100 ตัวอักษร)")
            elif kind not in TYPES:
                flash("ชนิดฟิลด์ไม่ถูกต้อง")
            elif raw_type and (type_id is None or db.execute("SELECT 1 FROM hazard_types WHERE id=?", (type_id,)).fetchone() is None):
                flash("ไม่พบประเภทอันตรายที่เลือก")
            elif opt_err:
                flash(opt_err)
            elif db.execute("SELECT 1 FROM custom_fields WHERE key=? AND ((? IS NULL AND hazard_type_id IS NOT NULL) "
                            "OR (? IS NOT NULL AND hazard_type_id IS NULL))", (key, type_id, type_id)).fetchone():
                flash("รหัสฟิลด์นี้ชนกับฟิลด์ของ 'ทุกประเภท' หรือของประเภทอื่นที่ใช้ร่วมกันได้ — ใช้รหัสอื่น")
            else:
                try:
                    db.execute("INSERT INTO custom_fields(hazard_type_id,key,label_th,type,options,required,sort) "
                               "VALUES(?,?,?,?,?,?,?)", (type_id, key, label, kind, options,
                                                         1 if f.get("required") not in (None, "", "0") else 0,
                                                         _int(f.get("sort")) or 0))
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("มีรหัสฟิลด์นี้อยู่แล้วในประเภทเดียวกัน")
        else:
            row = db.execute("SELECT * FROM custom_fields WHERE id=?", (_int(f.get("id")),)).fetchone()
            if row is None:
                flash("ไม่พบฟิลด์")
            elif action == "toggle":
                db.execute("UPDATE custom_fields SET active = 1 - active WHERE id=?", (row["id"],))
                db.commit()
            elif action == "update":          # รหัส/ชนิด/ประเภทอันตรายแก้ไม่ได้หลังสร้าง (กันค่าเดิมเสียหาย)
                label = f.get("label_th", "").strip()
                options, opt_err = _options_or_error(row["type"], f.get("options", ""))
                if not label or len(label) > 100:
                    flash("กรุณากรอกชื่อฟิลด์ (ไม่เกิน 100 ตัวอักษร)")
                elif opt_err:
                    flash(opt_err)
                else:
                    db.execute("UPDATE custom_fields SET label_th=?, options=?, required=?, sort=? WHERE id=?",
                               (label, options, 1 if f.get("required") not in (None, "", "0") else 0,
                                _int(f.get("sort")) or 0, row["id"]))
                    db.commit()
        return redirect("/admin/custom-fields")
    rows = db.execute("""SELECT c.*, t.name_th AS type_name FROM custom_fields c
                         LEFT JOIN hazard_types t ON t.id = c.hazard_type_id ORDER BY COALESCE(t.code, ''), c.sort, c.id""").fetchall()
    return render_template("custom_fields.html", fields=rows, types=TYPES,
                           hazard_types=db.execute("SELECT id, code, name_th FROM hazard_types WHERE active=1 ORDER BY code").fetchall())
