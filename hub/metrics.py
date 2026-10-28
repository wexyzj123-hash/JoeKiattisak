"""hub/metrics.py — ตัวชี้วัดสิ่งแวดล้อมและ Utility บนแดชบอร์ด Hub: ตัวชี้วัด (แอดมินแก้ได้) + ค่าที่บันทึก/นำเข้า CSV
ค่าตั้งต้นใส่ครั้งเดียว (ไม่ใส่กลับถ้าแอดมินลบ/ปิดแล้ว); ไม่มีข้อมูลตัวอย่างปลอมในฐานข้อมูลจริง"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date

ZONES = {"env": "สิ่งแวดล้อม", "utility": "Utility"}
KEY_RE = re.compile(r"^[a-z0-9_]{2,32}$")
CSV_MAX = 500_000
TRACE_POINTS = 12
# (key, zone, ชื่อ, หน่วย, ทิศทางที่ดี, เป้าหมาย, ทศนิยม, ลำดับ)
DEFAULTS = (
    ("electricity", "utility", "ไฟฟ้า", "kWh", "lower", None, 0, 10),
    ("water", "utility", "น้ำประปา", "m³", "lower", None, 0, 20),
    ("lpg", "utility", "ก๊าซ LPG", "kg", "lower", None, 0, 30),
    ("energy_intensity", "utility", "พลังงานต่อชิ้นงาน", "kWh/ชิ้น", "lower", None, 2, 40),
    ("cod", "env", "น้ำทิ้ง COD", "mg/L", "lower", 120.0, 0, 10),
    ("hazardous_waste", "env", "ของเสียอันตราย", "kg", "lower", None, 0, 20),
    ("recycle_rate", "env", "อัตรารีไซเคิลของเสีย", "%", "higher", None, 1, 30),
    ("co2e", "env", "ก๊าซเรือนกระจก (CO₂e)", "tCO₂e", "lower", None, 1, 40),
)


def seed_defaults(con) -> None:
    if con.execute("SELECT 1 FROM meta WHERE key='metrics_seeded'").fetchone() is None:
        con.executemany("INSERT OR IGNORE INTO metrics(key,zone,name_th,unit,direction,target,decimals,sort) VALUES(?,?,?,?,?,?,?,?)", DEFAULTS)
        con.execute("INSERT INTO meta(key,value) VALUES('metrics_seeded','1')")
    con.commit()


def _status(latest, target, direction) -> str:
    if latest is None:
        return "none"
    if target is None:
        return "ok"
    ratio = latest / target if target else (0 if latest == 0 else math.inf)
    if direction == "lower":
        return "ok" if ratio <= 1 else ("warn" if ratio <= 1.1 else "bad")
    return "ok" if ratio >= 1 else ("warn" if ratio >= 0.9 else "bad")


def zone_view(db, zone: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for m in db.execute("SELECT * FROM metrics WHERE zone=? AND active=1 ORDER BY sort, id", (zone,)).fetchall():
        rows = db.execute("SELECT read_date, value FROM metric_readings WHERE metric_id=? AND read_date <= ? ORDER BY read_date DESC LIMIT ?",
                          (m["id"], today.isoformat(), TRACE_POINTS)).fetchall()
        trace = [r["value"] for r in reversed(rows)]
        latest = rows[0]["value"] if rows else None
        prev = rows[1]["value"] if len(rows) > 1 else None
        delta = round((latest - prev) / prev * 100, 1) if latest is not None and prev not in (None, 0) else None
        out.append({"key": m["key"], "name": m["name_th"], "unit": m["unit"], "decimals": m["decimals"], "target": m["target"], "direction": m["direction"],
                    "latest": latest, "latest_date": rows[0]["read_date"] if rows else None, "prev": prev, "delta_pct": delta, "trace": trace,
                    "status": _status(latest, m["target"], m["direction"])})
    return out


def _number(raw, low=0.0, high=1e12):
    try:
        v = float(str(raw).strip())
    except ValueError:
        return None
    return v if math.isfinite(v) and low <= v <= high else None


def save_reading(db, user, key: str, read_date: str, raw_value, today: date | None = None) -> list[str]:
    today = today or date.today()
    m = db.execute("SELECT id FROM metrics WHERE key=?", ((key or "").strip(),)).fetchone()
    if m is None:
        return ["ไม่พบตัวชี้วัดนี้"]
    try:
        d = date.fromisoformat((read_date or "").strip())
    except ValueError:
        return ["วันที่ไม่ถูกต้อง (ใช้รูปแบบ ปปปป-ดด-วว)"]
    if d > today:
        return ["วันที่เป็นอนาคต บันทึกไม่ได้"]
    value = _number(raw_value)
    if value is None:
        return ["ค่าต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป"]
    db.execute("INSERT INTO metric_readings(metric_id, read_date, value, entered_by) VALUES(?,?,?,?) "
               "ON CONFLICT(metric_id, read_date) DO UPDATE SET value=excluded.value, entered_by=excluded.entered_by, entered_at=datetime('now','localtime')",
               (m["id"], d.isoformat(), value, user["id"]))
    db.commit()
    return []


def import_csv(db, user, text: str, today: date | None = None) -> tuple[int, list[str]]:
    """คอลัมน์: รหัสตัวชี้วัด, วันที่, ค่า (มีหัวตารางหรือไม่ก็ได้); บรรทัดที่ผิดถูกข้ามพร้อมรายงานเหตุผล ส่วนที่ถูกบันทึกให้"""
    if len(text or "") > CSV_MAX:
        return 0, ["ไฟล์ใหญ่เกินไป"]
    ok, errors = 0, []
    for n, row in enumerate(csv.reader(io.StringIO(text or "")), 1):
        if not row or not any(c.strip() for c in row):
            continue
        if n == 1 and row[0].strip().lower() in ("metric", "key", "รหัส", "ตัวชี้วัด"):
            continue
        if len(row) != 3:
            errors.append(f"บรรทัด {n}: ต้องมี 3 คอลัมน์ (รหัส, วันที่, ค่า)")
            continue
        err = save_reading(db, user, row[0], row[1], row[2], today)
        if err:
            errors.append(f"บรรทัด {n}: {err[0]}")
        else:
            ok += 1
    return ok, errors


def save_metric(db, form) -> list[str]:
    key, zone, name = (form.get("key") or "").strip(), (form.get("zone") or "").strip(), (form.get("name_th") or "").strip()
    unit, direction = (form.get("unit") or "").strip(), (form.get("direction") or "lower").strip()
    if not KEY_RE.match(key):
        return ["รหัสใช้ได้เฉพาะ a-z 0-9 _ ความยาว 2-32 ตัวอักษร"]
    if zone not in ZONES:
        return ["กลุ่มต้องเป็นสิ่งแวดล้อมหรือ Utility"]
    if not name or len(name) > 80:
        return ["กรุณากรอกชื่อตัวชี้วัด (ไม่เกิน 80 ตัวอักษร)"]
    if len(unit) > 20:
        return ["หน่วยยาวเกิน 20 ตัวอักษร"]
    if direction not in ("lower", "higher"):
        return ["ทิศทางที่ดีต้องเป็นต่ำกว่าเป้า หรือสูงกว่าเป้า"]
    raw_target = (form.get("target") or "").strip()
    target = _number(raw_target) if raw_target else None
    if raw_target and target is None:
        return ["เป้าหมายต้องเป็นตัวเลขตั้งแต่ 0 ขึ้นไป"]
    try:
        decimals, sort = int(form.get("decimals") or 0), int(form.get("sort") or 0)
    except ValueError:
        return ["ทศนิยมและลำดับต้องเป็นจำนวนเต็ม"]
    if not 0 <= decimals <= 4:
        return ["ทศนิยมต้องอยู่ระหว่าง 0-4"]
    db.execute("INSERT INTO metrics(key,zone,name_th,unit,direction,target,decimals,sort) VALUES(?,?,?,?,?,?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET zone=excluded.zone, name_th=excluded.name_th, unit=excluded.unit, direction=excluded.direction, "
               "target=excluded.target, decimals=excluded.decimals, sort=excluded.sort", (key, zone, name, unit, direction, target, decimals, sort))
    db.commit()
    return []


def toggle_metric(db, key: str) -> list[str]:
    if db.execute("UPDATE metrics SET active = 1 - active WHERE key=?", ((key or "").strip(),)).rowcount != 1:
        return ["ไม่พบตัวชี้วัดนี้"]
    db.commit()
    return []
