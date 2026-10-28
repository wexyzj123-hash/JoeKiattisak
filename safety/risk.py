"""safety/risk.py — เกณฑ์ความเสี่ยง: คะแนน = โอกาส x ความรุนแรง; ระดับความเสี่ยงต้องครอบคลุมทุกคะแนนพอดี"""
from __future__ import annotations

import re

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def scale_size(db, kind: str) -> int:
    return db.execute("SELECT COALESCE(MAX(level), 0) FROM risk_scales WHERE kind=?", (kind,)).fetchone()[0]


def max_score(db) -> int:
    return scale_size(db, "likelihood") * scale_size(db, "severity")


def list_levels(db) -> list:
    return db.execute("SELECT * FROM risk_levels ORDER BY min_score, id").fetchall()


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def calc_score(db, likelihood: int, severity: int) -> int:
    if not (_is_int(likelihood) and _is_int(severity)):
        raise ValueError("โอกาสและความรุนแรงต้องเป็นจำนวนเต็ม")
    if not 1 <= likelihood <= scale_size(db, "likelihood") or not 1 <= severity <= scale_size(db, "severity"):
        raise ValueError("ค่าโอกาส/ความรุนแรงอยู่นอกเกณฑ์")
    return likelihood * severity


def level_for_score(db, score: int):
    return db.execute("SELECT * FROM risk_levels WHERE ? BETWEEN min_score AND max_score ORDER BY min_score LIMIT 1",
                      (score,)).fetchone()


def validate_levels(levels: list[dict], max_score: int) -> list[str]:
    """ตรวจชุดระดับความเสี่ยง: ชื่อ/สี/SLA ถูกต้อง และช่วงคะแนน 1..max_score ครอบคลุมครบ ไม่ซ้อน ไม่ขาด"""
    if not levels:
        return ["ต้องมีระดับความเสี่ยงอย่างน้อย 1 ระดับ"]
    errors: list[str] = []
    names: set[str] = set()
    ranges: list[tuple[int, int, str]] = []
    for lv in levels:
        name = str(lv.get("name_th") or "").strip()
        label = name or "(ไม่มีชื่อ)"
        if not name:
            errors.append("กรุณากรอกชื่อระดับความเสี่ยงทุกระดับ")
        elif name in names:
            errors.append(f"ชื่อระดับซ้ำ: {name}")
        names.add(name)
        if not COLOR_RE.match(str(lv.get("color") or "")):
            errors.append(f"สีของระดับ {label} ต้องเป็นรูปแบบ #RRGGBB")
        sla = lv.get("sla_days")
        if not _is_int(sla) or sla < 0:
            errors.append(f"SLA (วัน) ของระดับ {label} ต้องเป็นจำนวนเต็มตั้งแต่ 0")
        lo, hi = lv.get("min_score"), lv.get("max_score")
        if not (_is_int(lo) and _is_int(hi)) or lo < 1 or hi > max_score or lo > hi:
            errors.append(f"ช่วงคะแนนของระดับ {label} ต้องอยู่ในช่วง 1-{max_score} และต่ำสุดไม่เกินสูงสุด")
        else:
            ranges.append((lo, hi, label))
    ranges.sort()
    expect = 1
    for lo, hi, label in ranges:
        if lo < expect:
            errors.append(f"ช่วงคะแนนของระดับ {label} ซ้อนกับระดับก่อนหน้า")
        elif lo > expect:
            errors.append(f"มีช่องว่างของคะแนน {expect}-{lo - 1} ที่ไม่มีระดับความเสี่ยง")
        expect = max(expect, hi + 1)
    if ranges and expect <= max_score:
        errors.append(f"มีช่องว่างของคะแนน {expect}-{max_score} ที่ไม่มีระดับความเสี่ยง")
    return errors


# ---------- หน้าจอเกณฑ์ความเสี่ยง: บันทึกทั้งฟอร์มในครั้งเดียว (atomic) ----------
MAX_SCALE_ROWS = 10
EXTRA_LEVEL_ROWS = 3
_INT_RE = re.compile(r"^\s*-?\d+\s*$")


def _int(raw) -> int | None:
    return int(raw) if raw is not None and _INT_RE.match(str(raw)) else None


def criteria_form_values(db) -> dict[str, str]:
    """ค่าเริ่มต้นของฟอร์มจากฐานข้อมูล (ชื่อฟิลด์เดียวกับที่ apply_criteria อ่าน) — แถวมาตราส่วนครบ 10 แถว + แถวระดับว่างให้เพิ่ม"""
    f: dict[str, str] = {"likelihood_size": str(scale_size(db, "likelihood")),
                         "severity_size": str(scale_size(db, "severity"))}
    for kind in ("likelihood", "severity"):
        rows = {r["level"]: r for r in db.execute("SELECT * FROM risk_scales WHERE kind=?", (kind,))}
        for n in range(1, MAX_SCALE_ROWS + 1):
            f[f"{kind}_label_{n}"] = rows[n]["label_th"] if n in rows else ""
            f[f"{kind}_desc_{n}"] = rows[n]["description"] if n in rows else ""
    levels = list_levels(db)
    f["level_count"] = str(len(levels) + EXTRA_LEVEL_ROWS)
    for i in range(len(levels) + EXTRA_LEVEL_ROWS):
        lv = levels[i] if i < len(levels) else None
        f[f"level_id_{i}"] = str(lv["id"]) if lv else ""
        f[f"level_name_{i}"] = lv["name_th"] if lv else ""
        f[f"level_min_{i}"] = str(lv["min_score"]) if lv else ""
        f[f"level_max_{i}"] = str(lv["max_score"]) if lv else ""
        f[f"level_color_{i}"] = lv["color"] if lv else ""
        f[f"level_sla_{i}"] = str(lv["sla_days"]) if lv else ""
        f[f"level_approval_{i}"] = "1" if lv and lv["requires_approval"] else ""
    return f


def apply_criteria(db, form) -> list[str]:
    errors: list[str] = []
    sizes: dict[str, int | None] = {}
    labels: list[tuple[str, int, str, str]] = []
    for kind, th in (("likelihood", "โอกาส"), ("severity", "ความรุนแรง")):
        size = _int(form.get(f"{kind}_size"))
        if size is None or not 2 <= size <= MAX_SCALE_ROWS:
            errors.append(f"ขนาดมาตราส่วน{th}ต้องเป็นจำนวนเต็ม 2-{MAX_SCALE_ROWS}")
            sizes[kind] = None
            continue
        sizes[kind] = size
        for n in range(1, size + 1):
            label = (form.get(f"{kind}_label_{n}") or "").strip()
            desc = (form.get(f"{kind}_desc_{n}") or "").strip()
            if not label:
                errors.append(f"กรุณากรอกชื่อระดับ{th}ที่ {n}")
            elif len(label) > 100 or len(desc) > 300:
                errors.append(f"ชื่อ/คำอธิบายระดับ{th}ที่ {n} ยาวเกินกำหนด (ชื่อ ≤ 100, คำอธิบาย ≤ 300)")
            labels.append((kind, n, label, desc))
    count = _int(form.get("level_count"))
    if count is None or not 0 <= count <= 30:
        errors.append("จำนวนแถวระดับความเสี่ยงไม่ถูกต้อง")
        count = 0
    levels, deleted_ids, kept_ids = [], set(), set()
    existing_ids = {r["id"] for r in db.execute("SELECT id FROM risk_levels")}
    for i in range(count):
        g = lambda k: (form.get(f"level_{k}_{i}") or "").strip()
        lid = _int(g("id")) if g("id") else None
        if g("id") and (lid is None or lid not in existing_ids):
            errors.append("ไม่พบระดับความเสี่ยงที่ต้องการแก้ไข")
            continue
        if g("delete") not in ("", "0"):
            if lid is not None:
                deleted_ids.add(lid)
            continue
        if lid is None and not (g("name") or g("min") or g("max") or g("color") or g("sla")):
            continue                                                       # แถวว่างสำหรับเพิ่มระดับ — ข้าม
        if lid is not None:
            kept_ids.add(lid)
        levels.append({"id": lid, "name_th": g("name"), "min_score": _int(g("min")), "max_score": _int(g("max")),
                       "color": g("color"), "sla_days": _int(g("sla")),
                       "requires_approval": 1 if g("approval") not in ("", "0") else 0})
    if all(sizes.values()):
        errors += validate_levels(levels, sizes["likelihood"] * sizes["severity"])
        used = db.execute("SELECT MAX(likelihood), MAX(severity) FROM hazards").fetchone()
        for kind, v in (("likelihood", used[0]), ("severity", used[1])):
            if v and v > sizes[kind]:
                errors.append(f"ลดขนาดมาตราส่วนเหลือ {sizes[kind]} ไม่ได้ เพราะมีรายการอันตรายที่ใช้ค่า {v} อยู่")
    if errors:
        return errors
    try:
        for kind in ("likelihood", "severity"):
            db.execute("DELETE FROM risk_scales WHERE kind=? AND level>?", (kind, sizes[kind]))
        for kind, n, label, desc in labels:
            db.execute("INSERT INTO risk_scales(kind, level, label_th, description) VALUES(?,?,?,?) "
                       "ON CONFLICT(kind, level) DO UPDATE SET label_th=excluded.label_th, description=excluded.description",
                       (kind, n, label, desc))
        gone = existing_ids - kept_ids
        for lid in gone:
            db.execute("UPDATE hazards SET risk_level_id=NULL WHERE risk_level_id=?", (lid,))       # ประวัติยังอยู่ใน snapshot
            db.execute("DELETE FROM risk_levels WHERE id=?", (lid,))
        for lid in kept_ids:                                               # ชื่อชั่วคราวก่อน — กันชนกันตอนสลับชื่อ
            db.execute("UPDATE risk_levels SET name_th=? WHERE id=?", (f"__tmp_{lid}", lid))
        for pos, lv in enumerate(levels):
            args = (lv["name_th"], lv["min_score"], lv["max_score"], lv["color"], lv["sla_days"],
                    lv["requires_approval"], pos)
            if lv["id"] is None:
                db.execute("INSERT INTO risk_levels(name_th,min_score,max_score,color,sla_days,requires_approval,sort) "
                           "VALUES(?,?,?,?,?,?,?)", args)
            else:
                db.execute("UPDATE risk_levels SET name_th=?, min_score=?, max_score=?, color=?, sla_days=?, "
                           "requires_approval=?, sort=? WHERE id=?", args + (lv["id"],))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return []
