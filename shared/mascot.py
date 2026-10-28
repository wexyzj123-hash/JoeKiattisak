"""shared/mascot.py — "น้องเอเอียต" มาสคอตการ์ตูนหมวกนิรภัยของ AIAT (ออกแบบใหม่ ไม่ใช้ภาพจากภายนอก)
วาดเป็น SVG แบบ inline ล้วน: ไม่มีไฟล์ภายนอก ไม่มี <script> ไม่มี id (วางหลายตัวในหน้าเดียวไม่ชนกัน)
ท่าทาง (mood): default ยิ้มทักทาย · alert ชูป้ายเตือน · celebrate ฉลอง · sleeping หลับ (ไม่มีรายการ) · thinking ครุ่นคิด (ไม่พบผลลัพธ์)
แอนิเมชันทำด้วย CSS ใน theme.css (class mascot-*) และปิดเมื่อผู้ใช้ตั้ง prefers-reduced-motion"""
from __future__ import annotations

from xml.sax.saxutils import escape

MOODS = ("default", "alert", "celebrate", "sleeping", "thinking")
DEFAULT_LABEL = "น้องเอเอียต"

INK = "#1b2433"
SKIN = "#ffd9b3"
HAT = "#ffb703"
HAT_DARK = "#f08c00"
BLUE = "#1d4ed8"
BLUE_DARK = "#17358f"
VEST = "#ff9f1c"
SILVER = "#f4f7fb"
CHEEK = "#ff8fa3"
RED = "#ef4444"
GREEN = "#22c55e"
SKY = "#38bdf8"
PINK = "#f472b6"
YELLOW = "#fde047"


def _clamp_size(size) -> int:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return 120
    if isinstance(size, bool):
        return 120
    return max(16, min(512, n))


# ---------- ชิ้นส่วนร่วม ----------
def _shadow() -> str:
    return '<ellipse cx="100" cy="188" rx="46" ry="6" fill="#000" opacity=".18"/>'


def _legs() -> str:
    return (f'<rect x="78" y="168" width="16" height="16" rx="7" fill="{BLUE_DARK}"/>'
            f'<rect x="106" y="168" width="16" height="16" rx="7" fill="{BLUE_DARK}"/>'
            f'<ellipse cx="85" cy="184" rx="13" ry="5.5" fill="{INK}"/><ellipse cx="115" cy="184" rx="13" ry="5.5" fill="{INK}"/>')


def _body() -> str:
    return (f'<rect x="64" y="120" width="72" height="56" rx="24" fill="{BLUE}"/>'
            # เสื้อสะท้อนแสง: พื้นส้ม + แถบเงินสองเส้น
            f'<path d="M78 122 H122 L126 172 H74 Z" fill="{VEST}"/>'
            f'<rect x="74" y="138" width="52" height="7" rx="3" fill="{SILVER}"/>'
            f'<rect x="75" y="156" width="50" height="7" rx="3" fill="{SILVER}"/>'
            f'<path d="M92 122 L100 134 L108 122" fill="{BLUE_DARK}"/>')


def _limb(x1: float, y1: float, x2: float, y2: float) -> str:
    return f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{BLUE}" stroke-width="16" stroke-linecap="round" fill="none"/>'


def _hand(x: float, y: float) -> str:
    return f'<circle cx="{x}" cy="{y}" r="9" fill="{SKIN}"/>'


def _arm_down(side: str) -> str:
    sx, hx = (68, 55) if side == "l" else (132, 145)
    return f'<g class="m-arm-{side}">{_limb(sx, 136, hx, 156)}{_hand(hx, 162)}</g>'


def _arm_up(side: str, reach: int = 0) -> str:
    """แขนชูขึ้น: ต่อจากไหล่ถึงมือเป็นเส้นเดียว (reach ยิ่งมากยิ่งชูสูง)"""
    sx, hx = (68, 40 - reach // 2) if side == "l" else (132, 160 + reach // 2)
    hy = 100 - reach
    return f'<g class="m-arm-{side}">{_limb(sx, 134, hx, hy + 4)}{_hand(hx - (3 if side == "l" else -3), hy)}</g>'


def _head() -> str:
    return (f'<circle cx="100" cy="100" r="44" fill="{SKIN}"/>'
            f'<ellipse cx="57" cy="104" rx="5" ry="8" fill="{SKIN}"/><ellipse cx="143" cy="104" rx="5" ry="8" fill="{SKIN}"/>')


def _helmet() -> str:
    return (f'<path d="M50 92 A50 50 0 0 1 150 92 Z" fill="{HAT}"/>'
            f'<path d="M92 44 H108 V92 H92 Z" fill="#ffd45a"/>'                                  # สันหมวก
            f'<path d="M60 84 A40 40 0 0 1 70 62" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" opacity=".5"/>'  # แสงสะท้อน
            f'<rect x="40" y="88" width="120" height="13" rx="6.5" fill="{HAT_DARK}"/>'           # ปีกหมวก
            f'<circle cx="100" cy="68" r="12" fill="#fff"/>'                                        # ป้าย AIAT
            f'<path d="M93.5 75 L100 60.5 L106.5 75" fill="none" stroke="{BLUE}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="M96 71.5 H104" stroke="{BLUE}" stroke-width="3" stroke-linecap="round"/>')


def _cheeks() -> str:
    return (f'<ellipse cx="76" cy="112" rx="7" ry="4.5" fill="{CHEEK}" opacity=".65"/>'
            f'<ellipse cx="124" cy="112" rx="7" ry="4.5" fill="{CHEEK}" opacity=".65"/>')


def _eyes_open(r: float = 6.2, dx: float = 0, dy: float = 0) -> str:
    out = ""
    for cx in (86, 114):
        out += (f'<ellipse cx="{cx}" cy="108" rx="{r - 1.2}" ry="{r + 0.8}" fill="{INK}"/>'
                f'<circle cx="{cx + 1.8 + dx}" cy="{105.5 + dy}" r="2.1" fill="#fff"/>')
    return out


def _eyes_happy() -> str:
    return "".join(f'<path d="M{cx - 6} 109 Q{cx} 100 {cx + 6} 109" fill="none" stroke="{INK}" stroke-width="3.4" stroke-linecap="round"/>'
                   for cx in (86, 114))


def _eyes_sleep() -> str:
    return "".join(f'<path d="M{cx - 6} 106 Q{cx} 113 {cx + 6} 106" fill="none" stroke="{INK}" stroke-width="3.2" stroke-linecap="round"/>'
                   for cx in (86, 114))


def _brows(kind: str) -> str:
    if kind == "worried":
        return (f'<path d="M77 100 L93 94" stroke="{INK}" stroke-width="3.2" stroke-linecap="round"/>'      # ปลายใน (ใกล้จมูก) สูงกว่า = กังวล
                f'<path d="M123 100 L107 94" stroke="{INK}" stroke-width="3.2" stroke-linecap="round"/>')
    if kind == "think":
        return (f'<path d="M78 98 Q86 94 94 97" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'
                f'<path d="M106 92 Q114 87 122 92" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>')
    return ""


def _mouth(kind: str) -> str:
    if kind == "smile":
        return f'<path d="M91 120 Q100 130 109 120" fill="none" stroke="{INK}" stroke-width="3.2" stroke-linecap="round"/>'
    if kind == "open":
        return f'<path d="M89 118 Q100 138 111 118 Z" fill="{INK}"/><path d="M93 124 Q100 130 107 124 Q100 128 93 124Z" fill="{RED}"/>'
    if kind == "o":
        return f'<ellipse cx="100" cy="124" rx="5" ry="6.5" fill="{INK}"/>'
    if kind == "flat":
        return f'<path d="M93 123 Q100 125 107 123" fill="none" stroke="{INK}" stroke-width="3.2" stroke-linecap="round"/>'
    if kind == "wavy":
        return f'<path d="M91 123 q4.5 -4 9 0 t9 0" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'
    return ""


# ---------- ท่าทางต่าง ๆ ----------
def _scene(mood: str) -> str:
    base_pre = _shadow() + _legs()
    if mood == "alert":
        sign = (f'<g class="m-sign"><rect x="170" y="48" width="4.5" height="26" rx="2" fill="#8a5a2b"/>'
                f'<path d="M172 10 L194 48 H150 Z" fill="{RED}" stroke="#fff" stroke-width="4" stroke-linejoin="round"/>'
                f'<rect x="169.8" y="22" width="4.4" height="14" rx="2.2" fill="#fff"/><circle cx="172" cy="42" r="2.8" fill="#fff"/></g>')
        extras = f'<path d="M58 92 q-6 8 0 14 q6 -6 0 -14Z" fill="{SKY}" opacity=".95"/>'            # หยดเหงื่อ
        return (base_pre + _arm_down("l") + _arm_up("r", 22) + _body() + _head() + _helmet() + _brows("worried") + _eyes_open(7, 0, 0)
                + _cheeks() + _mouth("o") + extras + sign)
    if mood == "celebrate":
        conf = "".join(f'<rect x="{x}" y="{y}" width="7" height="12" rx="2" fill="{c}" transform="rotate({r} {x + 3} {y + 6})"/>'
                       for x, y, c, r in ((20, 30, PINK, 25), (170, 26, SKY, -30), (34, 66, GREEN, -15), (160, 62, YELLOW, 40),
                                          (100, 10, RED, 10), (62, 18, SKY, -40), (140, 12, GREEN, 30)))
        stars = (f'<path d="M24 100 l3 7 7 1 -5 5 1.5 7 -6.5 -4 -6.5 4 1.5 -7 -5 -5 7 -1z" fill="{YELLOW}"/>'
                 f'<path d="M176 104 l2.4 5.6 5.6 .8 -4 4 1.2 5.6 -5.2 -3.2 -5.2 3.2 1.2 -5.6 -4 -4 5.6 -.8z" fill="{YELLOW}"/>')
        return (base_pre + _arm_up("l", 30) + _arm_up("r", 30) + _body() + _head() + _helmet() + _eyes_happy() + _cheeks() + _mouth("open")
                + f'<g class="m-confetti">{conf}{stars}</g>')
    if mood == "sleeping":
        z = (f'<g class="m-z" fill="none" stroke="{SKY}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M142 56 h14 l-14 16 h14"/><path d="M162 30 h10 l-10 12 h10" opacity=".8"/></g>')
        return (base_pre + _arm_down("l") + _arm_down("r") + _body() + _head() + _helmet() + _eyes_sleep() + _cheeks() + _mouth("flat") + z)
    if mood == "thinking":
        hand = f'<g class="m-hand">{_limb(139, 152, 125, 133)}{_hand(122, 128)}</g>'
        dots = (f'<circle cx="150" cy="56" r="3.4" fill="{SKY}"/><circle cx="162" cy="42" r="5.2" fill="{SKY}"/>'
                f'<circle cx="178" cy="24" r="8" fill="{SKY}" opacity=".9"/>')
        return (base_pre + _arm_down("l") + _body() + _head() + _helmet() + _brows("think") + _eyes_open(6, 2.4, -1.4) + _cheeks()
                + _mouth("wavy") + hand + f'<g class="m-dots">{dots}</g>')
    # default: ยิ้มทักทาย โบกมือ
    return (base_pre + _arm_down("l") + _arm_up("r", 12) + _body() + _head() + _helmet() + _eyes_open() + _cheeks() + _mouth("smile"))


def mascot_svg(mood: str = "default", size: int = 120, label: str = DEFAULT_LABEL) -> str:
    """SVG แบบ inline ของน้องเอเอียต; mood ที่ไม่รู้จัก → default; size จำกัด 16-512 (ค่าผิด → 120); label ถูก escape ใน <title>"""
    mood = mood if mood in MOODS else "default"
    px = _clamp_size(size)
    title = escape(str(label if label is not None else DEFAULT_LABEL), {'"': "&quot;", "'": "&#39;"})
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="mascot mascot-{mood}" viewBox="0 0 200 200" width="{px}" height="{px}" '
            f'role="img" focusable="false"><title>{title}</title>{_scene(mood)}</svg>')
