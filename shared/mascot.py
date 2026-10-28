"""shared/mascot.py — "น้องเอเอียต" มาสคอตชายหนุ่มสไตล์ชิบิ หมวกแก๊ปกรมท่า AISIN ชุดฟ้าแถบสะท้อนแสง (วาดใหม่ตามแผ่นมาสคอต AISIN)
วาดเป็น SVG แบบ inline ล้วน: ไม่มีไฟล์ภายนอก ไม่มี <script> ไม่มี id (วางหลายตัวในหน้าเดียวไม่ชนกัน)
ท่าทาง (mood): default ยิ้มแย้ม · confident มั่นใจ (iwink) · thinking ใส่ใจงาน (ถือคลิปบอร์ด) · celebrate สู้ไปด้วยกัน (ชูกำปั้น)
earth รักษ์โลก (โอบลูกโลก) · alert ปลอดภัยไว้ก่อน (หมวกนิรภัยสีขาว) · sleeping หลับ (ไม่มีรายการ)
แอนิเมชันทำด้วย CSS ใน theme.css (class mascot-*) และปิดเมื่อผู้ใช้ตั้ง prefers-reduced-motion"""
from __future__ import annotations

from xml.sax.saxutils import escape

from shared.brand import aisin_letters

MOODS = ("default", "confident", "thinking", "celebrate", "earth", "alert", "sleeping")
DEFAULT_LABEL = "น้องเอเอียต"

INK = "#1b2433"
SKIN = "#ffdcb8"
SKIN_LINE = "#e3b184"
HAIR = "#1f2430"
NAVY = "#12306b"
NAVY_DARK = "#0b1f4a"
BLUE = "#2166d6"
BLUE_LIGHT = "#3b82f0"
PANTS = "#1a4aa8"
SILVER = "#e9eff8"
CHEEK = "#ff8fa3"
RED = "#ef4444"
GREEN = "#22c55e"
SKY = "#38bdf8"
PINK = "#f472b6"
YELLOW = "#fde047"
WHITE = "#ffffff"


def _clamp_size(size) -> int:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return 120
    if isinstance(size, bool):
        return 120
    return max(16, min(512, n))


def _n(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


# ---------- ชิ้นส่วนร่วม ----------
def _shadow() -> str:
    return '<ellipse cx="100" cy="234" rx="54" ry="5" fill="#000" opacity=".16"/>'


def _legs() -> str:
    out = ""
    for x in (76, 102):
        out += (f'<rect x="{x}" y="198" width="22" height="26" rx="8" fill="{PANTS}"/>'
                f'<rect x="{x - 3}" y="219" width="28" height="12" rx="6" fill="{INK}"/>'
                f'<rect x="{x - 3}" y="228" width="28" height="4" rx="2" fill="{BLUE_LIGHT}"/>')
    return out


def _body() -> str:
    return (f'<rect x="62" y="130" width="76" height="74" rx="24" fill="{BLUE}"/>'
            f'<path d="M86 133 L100 152 L114 133 Z" fill="{SILVER}"/>'                              # คอปก
            f'<path d="M100 152 V203" stroke="{NAVY}" stroke-width="2" stroke-linecap="round"/>'   # ซิป
            f'<rect x="62" y="168" width="76" height="7" fill="{SILVER}"/>'                         # แถบสะท้อนแสง
            f'<rect x="62" y="180" width="76" height="7" fill="{SILVER}"/>'
            f'<rect x="70" y="145" width="19" height="9" rx="2.5" fill="{BLUE_LIGHT}" stroke="{NAVY}" stroke-width="1.2"/>'  # ฝากระเป๋า
            f'<rect x="108" y="142" width="23" height="13" rx="3" fill="{NAVY}"/>'                 # แพตช์อก
            + aisin_letters(111, 147, 14.5, WHITE))


def _limb(x1: float, y1: float, x2: float, y2: float) -> str:
    """แขนเสื้อสีฟ้าเส้นเดียวต่อเนื่อง + แถบสะท้อนแสงที่ข้อศอก"""
    a, b = 0.42, 0.58
    sx1, sy1, sx2, sy2 = x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b
    return (f'<path d="M{_n(x1)} {_n(y1)} L{_n(x2)} {_n(y2)}" stroke="{BLUE}" stroke-width="18" stroke-linecap="round" fill="none"/>'
            f'<path d="M{_n(sx1)} {_n(sy1)} L{_n(sx2)} {_n(sy2)}" stroke="{SILVER}" stroke-width="18" fill="none"/>')


def _hand(x: float, y: float, r: float = 9) -> str:
    return f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width="1.4"/>'


def _arm_down(side: str) -> str:
    if side == "l":
        return f'<g class="m-arm-l">{_limb(66, 148, 54, 184)}{_hand(53, 191)}</g>'
    return f'<g class="m-arm-r">{_limb(134, 148, 146, 184)}{_hand(147, 191)}</g>'


def _arm_hip() -> str:
    return (f'<g class="m-arm-l">{_limb(66, 148, 48, 170)}{_limb(48, 170, 68, 186)}{_hand(70, 187)}</g>')


def _thumb_up(wave: bool = True) -> str:
    cls = "m-arm-r" if wave else "m-arm-up"
    return (f'<g class="{cls}">{_limb(134, 148, 158, 128)}'
            f'<path d="M156 118 Q154 100 161 97 Q168 99 167 118 Z" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width="1.4" stroke-linejoin="round"/>'   # นิ้วโป้ง
            f'<rect x="150" y="113" width="24" height="20" rx="9" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width="1.4"/>'
            f'<path d="M155 120 H169 M155 126 H169" stroke="{SKIN_LINE}" stroke-width="1.4" stroke-linecap="round"/></g>')


def _fist_up() -> str:
    return (f'<g class="m-arm-r">{_limb(134, 148, 154, 104)}{_hand(156, 94, 12)}'
            f'<path d="M148 90 H164 M148 96 H164 M149 101 H163" stroke="{SKIN_LINE}" stroke-width="1.4" stroke-linecap="round"/></g>')


def _head() -> str:
    return (f'<ellipse cx="55" cy="104" rx="6" ry="9" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width="1.4"/>'
            f'<ellipse cx="145" cy="104" rx="6" ry="9" fill="{SKIN}" stroke="{SKIN_LINE}" stroke-width="1.4"/>'
            f'<ellipse cx="100" cy="92" rx="47" ry="45" fill="{SKIN}"/>'
            f'<path d="M54 84 Q50 108 58 118 L65 100 Z" fill="{HAIR}"/><path d="M146 84 Q150 108 142 118 L135 100 Z" fill="{HAIR}"/>'   # จอน
            f'<path d="M54 80 Q62 98 74 90 Q86 100 100 90 Q114 100 126 90 Q138 98 146 80 Z" fill="{HAIR}"/>')                        # ผมหน้าม้าใต้หมวก


def _cap() -> str:
    return (f'<path d="M52 82 C48 36 152 36 148 82 Z" fill="{NAVY}"/>'
            f'<path d="M100 48 V78" stroke="{BLUE}" stroke-width="1.6" opacity=".6"/>'
            f'<circle cx="100" cy="47" r="3.4" fill="{NAVY_DARK}"/>'
            + aisin_letters(72, 55, 56, WHITE) +
            f'<path d="M45 82 Q100 96 155 82 Q150 71 100 71 Q50 71 45 82 Z" fill="{NAVY_DARK}"/>'
            f'<path d="M45 82 Q100 96 155 82" fill="none" stroke="{WHITE}" stroke-width="2.4" stroke-linecap="round"/>')


def _hardhat() -> str:
    return (f'<path d="M52 82 C48 34 152 34 148 82 Z" fill="#f8fafd" stroke="#c9d3e3" stroke-width="2"/>'
            f'<path d="M91 48 H109 V80 H91 Z" fill="#e4eaf4" stroke="#c9d3e3" stroke-width="1.6"/>'
            f'<path d="M62 72 Q64 54 78 46" fill="none" stroke="{WHITE}" stroke-width="5" stroke-linecap="round" opacity=".9"/>'
            + aisin_letters(75, 60, 50, NAVY) +
            f'<path d="M45 83 Q100 97 155 83 Q150 72 100 72 Q50 72 45 83 Z" fill="#e9eef7" stroke="#c9d3e3" stroke-width="2"/>')


def _cheeks() -> str:
    return (f'<ellipse cx="68" cy="119" rx="8" ry="5" fill="{CHEEK}" opacity=".6"/>'
            f'<ellipse cx="132" cy="119" rx="8" ry="5" fill="{CHEEK}" opacity=".6"/>')


def _eye(cx: float, dx: float = 0, dy: float = 0) -> str:
    return (f'<ellipse cx="{cx}" cy="108" rx="9.6" ry="11.6" fill="{INK}"/>'
            f'<ellipse cx="{cx}" cy="111" rx="6" ry="6" fill="#3a4a7a" opacity=".7"/>'
            f'<circle cx="{_n(cx + 3 + dx)}" cy="{_n(103 + dy)}" r="3.4" fill="{WHITE}"/>'
            f'<circle cx="{_n(cx - 3.2 + dx)}" cy="{_n(113 + dy)}" r="1.7" fill="{WHITE}"/>')


def _eyes_open(dx: float = 0, dy: float = 0) -> str:
    return _eye(80, dx, dy) + _eye(120, dx, dy)


def _eye_happy(cx: float) -> str:
    return f'<path d="M{cx - 8} 112 Q{cx} 98 {cx + 8} 112" fill="none" stroke="{INK}" stroke-width="3.6" stroke-linecap="round"/>'


def _eye_sleep(cx: float) -> str:
    return f'<path d="M{cx - 8} 106 Q{cx} 116 {cx + 8} 106" fill="none" stroke="{INK}" stroke-width="3.4" stroke-linecap="round"/>'


def _nose() -> str:
    return f'<path d="M98 116 q2 2.4 4 0" fill="none" stroke="{SKIN_LINE}" stroke-width="1.6" stroke-linecap="round"/>'


def _mouth(kind: str) -> str:
    if kind == "open":
        return (f'<path d="M86 121 Q100 143 114 121 Q100 127 86 121 Z" fill="{INK}" stroke="{INK}" stroke-width="1.4" stroke-linejoin="round"/>'
                f'<path d="M93 131 Q100 137 107 131 Q100 127 93 131 Z" fill="{RED}"/>')
    if kind == "smile":
        return f'<path d="M90 122 Q100 132 110 122" fill="none" stroke="{INK}" stroke-width="3.2" stroke-linecap="round"/>'
    if kind == "flat":
        return f'<path d="M94 125 Q100 127 106 125" fill="none" stroke="{INK}" stroke-width="3" stroke-linecap="round"/>'
    return ""


def _clipboard() -> str:
    return (f'<rect x="78" y="152" width="44" height="52" rx="5" fill="#c98a4b" stroke="#8a5a2b" stroke-width="1.6"/>'
            f'<rect x="83" y="159" width="34" height="41" rx="2" fill="{WHITE}"/>'
            f'<rect x="91" y="148" width="18" height="9" rx="3" fill="#cbd5e1" stroke="#8a97ab" stroke-width="1.2"/>'
            f'<path d="M88 168 H112 M88 176 H108 M88 184 H104" stroke="#9fb0cc" stroke-width="2.2" stroke-linecap="round"/>'
            f'<path d="M100 191 l4 4 l9 -9" fill="none" stroke="{GREEN}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')


def _globe() -> str:
    return (f'<circle cx="100" cy="178" r="27" fill="{SKY}" stroke="#1b6fb8" stroke-width="2"/>'
            f'<path d="M84 166 Q92 158 102 162 Q108 170 98 176 Q96 186 88 184 Q80 176 84 166 Z" fill="{GREEN}"/>'
            f'<path d="M108 182 Q118 178 122 186 Q118 196 110 196 Q104 190 108 182 Z" fill="{GREEN}"/>'
            f'<path d="M80 158 Q86 154 92 155" fill="none" stroke="{WHITE}" stroke-width="3.4" stroke-linecap="round" opacity=".7"/>'
            f'<g class="m-leaf"><path d="M100 157 C91 151 92 140 103 138 C108 146 107 153 100 157 Z" fill="{GREEN}" stroke="#15803d" stroke-width="1.4" stroke-linejoin="round"/>'
            f'<path d="M100 156 Q100 148 102 142" fill="none" stroke="#15803d" stroke-width="1.4" stroke-linecap="round"/></g>')


# ---------- ท่าทางต่าง ๆ ----------
def _scene(mood: str) -> str:
    pre = _shadow() + _legs()
    if mood == "confident":
        eyes = _eye_happy(80) + _eye(120)                                                               # ขยิบตาข้างเดียว
        return (pre + _arm_hip() + _body() + _thumb_up(False) + _head() + _cap() + eyes + _nose() + _cheeks() + _mouth("smile")
                + f'<path d="M166 80 l2.4 5.4 5.4 .8 -3.9 3.8 1 5.4 -4.9 -2.7 -4.9 2.7 1 -5.4 -3.9 -3.8 5.4 -.8z" fill="{YELLOW}"/>')
    if mood == "thinking":
        return (pre + _body() + f'<g class="m-arm-l">{_limb(66, 150, 80, 186)}</g><g class="m-arm-r">{_limb(134, 150, 120, 186)}</g>'
                + _clipboard() + _hand(80, 188) + _hand(120, 188)
                + _head() + _cap() + _eyes_open(2.4, 2) + _nose() + _cheeks() + _mouth("smile"))
    if mood == "celebrate":
        conf = "".join(f'<rect x="{x}" y="{y}" width="7" height="12" rx="2" fill="{c}" transform="rotate({r} {x + 3} {y + 6})"/>'
                       for x, y, c, r in ((14, 40, PINK, 25), (176, 30, SKY, -30), (30, 80, GREEN, -15), (172, 74, YELLOW, 40),
                                          (100, 12, RED, 10), (58, 14, SKY, -40), (140, 10, GREEN, 30)))
        return (pre + _arm_down("l") + _body() + _fist_up() + _head() + _cap()
                + _eye_happy(80) + _eye_happy(120) + _nose() + _cheeks() + _mouth("open") + f'<g class="m-confetti">{conf}</g>')
    if mood == "earth":
        return (pre + _body() + f'<g class="m-arm-l">{_limb(66, 150, 76, 182)}</g><g class="m-arm-r">{_limb(134, 150, 124, 182)}</g>'
                + _globe() + _hand(76, 180) + _hand(124, 180)
                + _head() + _cap() + _eyes_open() + _nose() + _cheeks() + _mouth("smile"))
    if mood == "alert":
        spark = (f'<g class="m-glint"><path d="M164 52 l2.6 6 6 .9 -4.3 4.2 1 6 -5.3 -3 -5.3 3 1 -6 -4.3 -4.2 6 -.9z" fill="{YELLOW}"/>'
                 f'<path d="M34 60 l2 4.6 4.6 .7 -3.3 3.2 .8 4.6 -4.1 -2.3 -4.1 2.3 .8 -4.6 -3.3 -3.2 4.6 -.7z" fill="{YELLOW}"/></g>')
        return (pre + _arm_down("l") + _body() + _thumb_up() + _head() + _hardhat() + _eyes_open() + _nose() + _cheeks() + _mouth("open") + spark)
    if mood == "sleeping":
        z = (f'<g class="m-z" fill="none" stroke="{SKY}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">'
             '<path d="M150 62 h14 l-14 16 h14"/><path d="M170 36 h10 l-10 12 h10" opacity=".8"/></g>')
        return (pre + _arm_down("l") + _arm_down("r") + _body() + _head() + _cap() + _eye_sleep(80) + _eye_sleep(120)
                + _nose() + _cheeks() + _mouth("flat") + z)
    # default: ยิ้มแย้ม ชูนิ้วโป้ง
    return (pre + _arm_down("l") + _body() + _thumb_up() + _head() + _cap() + _eyes_open() + _nose() + _cheeks() + _mouth("open"))


def mascot_svg(mood: str = "default", size: int = 120, label: str = DEFAULT_LABEL, decorative: bool = False) -> str:
    """SVG แบบ inline ของน้องเอเอียต; mood ที่ไม่รู้จัก → default; size จำกัด 16-512 (ค่าผิด → 120);
    label ถูก escape ใน <title>; decorative=True → aria-hidden (ไม่มี role/title) สำหรับวางข้างข้อความ"""
    mood = mood if mood in MOODS else "default"
    px = _clamp_size(size)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" class="mascot mascot-{mood}" viewBox="0 0 200 240" width="{px}" height="{round(px * 1.2)}" ')
    if decorative:
        return head + f'aria-hidden="true" focusable="false">{_scene(mood)}</svg>'
    title = escape(str(label if label is not None else DEFAULT_LABEL), {'"': "&quot;", "'": "&#39;"})
    return head + f'role="img" focusable="false"><title>{title}</title>{_scene(mood)}</svg>'
