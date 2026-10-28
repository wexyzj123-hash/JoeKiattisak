"""safety/charts.py — กราฟ SVG ฝั่งเซิร์ฟเวอร์สำหรับหน้าหลัก (ไม่ใช้ไลบรารีภายนอก; ข้อความทุกส่วนถูก escape)"""
from __future__ import annotations

import math

from markupsafe import Markup, escape

PALETTE = ("#2166d6", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#64748b")
REPORTED, CLOSED = "#2166d6", "#16a34a"


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def donut(items: list[tuple[str, int]], size: int = 180) -> tuple[Markup, list[dict]]:
    """โดนัทของ [(ชื่อ, จำนวน)] → (svg, คำอธิบาย [{name, n, pct, color}]); ผลรวมเป็นศูนย์ → วงแหวนเทา"""
    total = sum(n for _, n in items)
    c, r, w = size / 2, size / 2 - 14, 22
    legend, paths, angle = [], "", -math.pi / 2
    pcts = [n * 100 // total if total else 0 for _, n in items]                         # เศษที่เหลือแจกให้ช่องที่เศษมากสุด ให้รวมเป็น 100 พอดี
    order = sorted(range(len(items)), key=lambda i: -((items[i][1] * 100) % total if total else 0))
    for i in order[:(100 - sum(pcts)) if total else 0]:
        pcts[i] += 1
    for i, (name, n) in enumerate(items):
        color = PALETTE[i % len(PALETTE)]
        legend.append({"name": name, "n": n, "pct": pcts[i], "color": color})
        if not total or n <= 0:
            continue
        if n == total:
            paths += f'<circle cx="{_n(c)}" cy="{_n(c)}" r="{_n(r)}" fill="none" stroke="{color}" stroke-width="{w}"/>'
            continue
        sweep = 2 * math.pi * n / total
        x1, y1 = c + r * math.cos(angle), c + r * math.sin(angle)
        x2, y2 = c + r * math.cos(angle + sweep), c + r * math.sin(angle + sweep)
        paths += (f'<path d="M{_n(x1)} {_n(y1)} A{_n(r)} {_n(r)} 0 {1 if sweep > math.pi else 0} 1 {_n(x2)} {_n(y2)}" '
                  f'fill="none" stroke="{color}" stroke-width="{w}"/>')
        angle += sweep
    base = f'<circle cx="{_n(c)}" cy="{_n(c)}" r="{_n(r)}" fill="none" stroke="var(--line)" stroke-width="{w}"/>'
    summary = "; ".join(f"{name} {n}" for name, n in items) or "ไม่มีรายการ"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" class="donut" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" '
           f'aria-label="{escape("รายการเปิดอยู่ตามประเภท: " + summary)}">{base}{paths}'
           f'<text x="{_n(c)}" y="{_n(c - 2)}" text-anchor="middle" font-size="26" font-weight="700" fill="currentColor">{total}</text>'
           f'<text x="{_n(c)}" y="{_n(c + 16)}" text-anchor="middle" font-size="11" fill="currentColor" opacity=".75">เปิดอยู่</text></svg>')
    return Markup(svg), legend


def trend(rows: list[tuple[str, int, int]], width: int = 600, height: int = 200) -> Markup:
    """แท่งคู่ แจ้ง/ปิด ต่อเดือน; rows = [(YYYY-MM, แจ้ง, ปิด)]"""
    peak = max([1] + [max(r, c) for _, r, c in rows])
    left, bottom, top = 8, 28, 10
    plot_h = height - bottom - top
    slot = (width - left * 2) / max(1, len(rows))
    bw = slot * 0.32
    parts = []
    for i, (month, reported, closed) in enumerate(rows):
        x = left + i * slot + slot * 0.14
        for j, (val, color, label) in enumerate(((reported, REPORTED, "แจ้ง"), (closed, CLOSED, "ปิด"))):
            h = plot_h * val / peak
            if val:
                parts.append(f'<rect x="{_n(x + j * (bw + 3))}" y="{_n(top + plot_h - h)}" width="{_n(bw)}" height="{_n(max(h, 2))}" rx="3" fill="{color}"><title>{escape(month)} {label} {val}</title></rect>')
        parts.append(f'<text x="{_n(left + i * slot + slot / 2)}" y="{height - 10}" text-anchor="middle" font-size="11" fill="currentColor" opacity=".8">{escape(month[5:])}</text>')
    summary = "; ".join(f"{m} แจ้ง {r} ปิด {c}" for m, r, c in rows)
    return Markup(f'<svg xmlns="http://www.w3.org/2000/svg" class="trend" viewBox="0 0 {width} {height}" role="img" '
                  f'aria-label="{escape("แนวโน้มแจ้ง/ปิด 12 เดือน: " + summary)}" preserveAspectRatio="xMidYMid meet">'
                  f'<path d="M{left} {top + plot_h} H{width - left}" stroke="var(--line)" stroke-width="1"/>{"".join(parts)}</svg>')
