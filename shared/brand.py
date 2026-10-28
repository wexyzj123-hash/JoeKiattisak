"""shared/brand.py — ตัวอักษร AISIN แบบเวกเตอร์ (เส้น path ล้วน ไม่พึ่งฟอนต์/ไฟล์ภายนอก) ใช้บนหมวกมาสคอต แพตช์ และแถบแบรนด์"""
from __future__ import annotations

from xml.sax.saxutils import escape

TITLE = "Safety 4.0"
TAGLINE = "Safe People · Safe Workplace · Sustainable Future"
FOOTER = "Safety Today, Better Tomorrow"


def _n(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def aisin_letters(x: float, y: float, w: float, fill: str = "currentColor", cls: str = "m-logo") -> str:
    """ชิ้นส่วน <g> ของคำว่า AISIN เอียงเล็กน้อย กว้างรวม w เริ่มที่ (x,y) สูง w/4.4"""
    h = w / 4.4
    slot = w / 5
    lw = slot * 0.74
    sw = max(h * 0.24, 0.6)
    lean = 0.2

    def P(px: float, py: float) -> str:
        return f"{_n(px + (y + h - py) * lean)} {_n(py)}"

    parts = []
    for i, ch in enumerate("AISIN"):
        x0 = x + i * slot
        if ch == "A":
            parts.append(f"M{P(x0, y + h)} L{P(x0 + lw / 2, y)} L{P(x0 + lw, y + h)}")
            parts.append(f"M{P(x0 + lw * .24, y + h * .68)} L{P(x0 + lw * .76, y + h * .68)}")
        elif ch == "I":
            parts.append(f"M{P(x0 + lw / 2, y)} L{P(x0 + lw / 2, y + h)}")
        elif ch == "S":
            parts.append(f"M{P(x0 + lw, y + h * .2)} C{P(x0 + lw * .75, y - h * .05)} {P(x0, y)} {P(x0, y + h * .3)} "
                         f"C{P(x0, y + h * .55)} {P(x0 + lw, y + h * .45)} {P(x0 + lw, y + h * .72)} "
                         f"C{P(x0 + lw, y + h * 1.05)} {P(x0 + lw * .25, y + h * 1.05)} {P(x0, y + h * .8)}")
        else:
            parts.append(f"M{P(x0, y + h)} L{P(x0, y)} L{P(x0 + lw, y + h)} L{P(x0 + lw, y)}")
    return (f'<g class="{cls}" fill="none" stroke="{fill}" stroke-width="{_n(sw)}" stroke-linejoin="round">'
            + "".join(f'<path d="{d}"/>' for d in parts) + "</g>")


def aisin_wordmark(width: int = 120, color: str = "currentColor") -> str:
    """โลโก้คำ AISIN แบบ inline SVG (role=img) ปรับตามความกว้าง"""
    try:
        px = max(24, min(600, int(width)))
    except (TypeError, ValueError):
        px = 120
    if isinstance(width, bool):
        px = 120
    color = color if isinstance(color, str) and color.replace("#", "").replace("-", "").isalnum() else "currentColor"
    height = round(px / 4.2)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="wordmark" viewBox="0 0 100 24" width="{px}" height="{height}" '
            f'role="img" focusable="false"><title>{escape("AISIN")}</title>'
            f'{aisin_letters(2, 3, 92, color, "wm")}</svg>')


def shield_logo(size: int = 40) -> str:
    """โล่ความปลอดภัยพร้อมเครื่องหมายถูก (ตกแต่ง — aria-hidden) ใช้คู่กับชื่อ Safety 4.0"""
    try:
        px = max(16, min(128, int(size)))
    except (TypeError, ValueError):
        px = 40
    if isinstance(size, bool):
        px = 40
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="shield-logo" viewBox="0 0 48 48" width="{px}" height="{px}" aria-hidden="true" focusable="false">'
            '<path d="M24 3l17 6v13c0 10-7 18-17 23C14 40 7 32 7 22V9z" fill="#ffffff"/>'
            '<path d="M24 7l13 4.6V22c0 7.8-5.2 14-13 18.4C16.2 36 11 29.800 11 22V11.600z" fill="#1f63d6"/>'
            '<path d="M17 24l5 5 9-10.500" fill="none" stroke="#ffffff" stroke-width="3.600" stroke-linecap="round" stroke-linejoin="round"/></svg>')
