"""hub/dashboard.py — ข้อมูลแดชบอร์ดหน้าแรกของ Hub: ความปลอดภัย (ดึงจาก Safety), สิ่งแวดล้อม, Utility (ตัวชี้วัดใน Hub), ปุ่มเข้าระบบ
ความผิดพลาดของระบบปลายทางต้องไม่ทำให้หน้า Hub พัง: ดึงไม่ได้ = แสดงสถานะ "ยังไม่เชื่อมต่อ" และใช้ค่าเก่าที่จำไว้ ≤ 5 นาที"""
from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen

from . import metrics

TTL = 30.0                 # วินาที — ไม่ยิง Safety ทุกครั้งที่เปิดหน้า
STALE = 300.0              # ใช้ค่าเก่าได้ไม่เกินนี้ถ้าดึงใหม่ไม่ได้
_cache: dict[str, tuple[float, dict]] = {}


def fetch_safety(url: str, service_token: str, timeout: float = 2.0, now: float | None = None) -> dict | None:
    now = time.monotonic() if now is None else now
    hit = _cache.get(url)
    if hit and now - hit[0] < TTL:
        return hit[1]
    try:
        req = Request(url.rstrip("/") + "/api/dashboard", headers={"X-Service-Token": service_token})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise ValueError("status")
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or "open_hazards" not in data:
            raise ValueError("shape")
        _cache[url] = (now, data)
        return data
    except Exception:                                   # noqa: BLE001 — ของเสริม ห้ามทำให้หน้าแรกล้ม
        return hit[1] if hit and now - hit[0] < STALE else None


def spark(values: list[float], w: int = 120, h: int = 34, pad: int = 3) -> str:
    """จุดของเส้นแนวโน้ม SVG (ค่าเดียว = เส้นตรงกลาง); ช่วงค่าเท่ากันหมด = เส้นแบนกลางกรอบ"""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = pad + (w - 2 * pad) * (i / (n - 1) if n > 1 else 0.5)
        y = h / 2 if hi == lo else pad + (h - 2 * pad) * (1 - (v - lo) / span)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def lamp(summary: dict | None) -> str:
    """ไฟ andon: red = ความเสี่ยงสูงค้าง/เพิ่งมีอุบัติเหตุหยุดงาน, amber = มีงานเกินกำหนด, green = ปกติ, off = ไม่มีข้อมูล"""
    if summary is None:
        return "off"
    if summary.get("high_risk_open") or summary.get("days_without_lost_time") == 0:
        return "red"
    if summary.get("overdue") or summary.get("capa_overdue"):
        return "amber"
    return "green"


LAMP_TEXT = {"red": "ต้องดำเนินการด่วน", "amber": "มีงานเกินกำหนด", "green": "ทุกอย่างอยู่ในกำหนด", "off": "ยังไม่เชื่อมต่อ Safety"}


def safety_lines(s: dict | None) -> list[tuple[str, int | str, str]]:
    """แถวตัวเลขด้านข้างป้าย: (ชื่อ, ค่า, น้ำเสียง ok|warn|bad|plain)"""
    if s is None:
        return []
    inc = s["incidents_month"]
    return [
        ("อันตรายที่ยังเปิดอยู่", s["open_hazards"], "plain"),
        ("เกินกำหนดแก้ไข", s["overdue"], "bad" if s["overdue"] else "ok"),
        ("ความเสี่ยงสูงที่ยังค้าง", s["high_risk_open"], "bad" if s["high_risk_open"] else "ok"),
        ("อุบัติเหตุเดือนนี้", inc["accident"], "warn" if inc["accident"] else "ok"),
        ("Near Miss เดือนนี้", inc["near_miss"], "plain"),
        ("CAPA ค้าง (เกินกำหนด)", f"{s['capa_open']} ({s['capa_overdue']})", "bad" if s["capa_overdue"] else "plain"),
        ("คะแนนตรวจ Patrol 30 วัน", f"{s['patrol_avg_score_30d']}%" if s["patrol_avg_score_30d"] is not None else "—", "plain"),
    ]


def build(db, apps: list[dict], safety_row, service_token: str, today=None) -> dict:
    s = fetch_safety(safety_row["url"], service_token) if safety_row is not None else None
    monthly = (s or {}).get("monthly") or []
    return {
        "safety": s, "lamp": lamp(s), "lamp_text": LAMP_TEXT[lamp(s)], "lines": safety_lines(s),
        "digits": [int(c) for c in str(s["days_without_lost_time"])] if s and s.get("days_without_lost_time") is not None else None,
        "trend": {"reported": spark([m["reported"] for m in monthly], 360, 70, 4), "closed": spark([m["closed"] for m in monthly], 360, 70, 4),
                  "labels": [m["m"][5:] for m in monthly], "max": max([m["reported"] for m in monthly] + [m["closed"] for m in monthly] + [0])},
        "env": metrics.zone_view(db, "env", today), "utility": metrics.zone_view(db, "utility", today), "apps": apps,
    }
