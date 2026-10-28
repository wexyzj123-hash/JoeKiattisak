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
