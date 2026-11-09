"""สร้าง hub/templates/_scene.html — ภาพเคลื่อนไหว 3 โซน (ความปลอดภัย · สิ่งแวดล้อม · Utility) แบบ SVG ล้วน ไม่มีไฟล์ภายนอก
ใช้: python tools/gen_scene.py  (ผลลัพธ์ commit ไว้แล้ว — รันซ้ำเมื่อต้องการแก้ภาพ; แอนิเมชันอยู่ใน hub/assets/scene.css)"""
import math
import os
import random

W, H, GROUND = 1200, 394, 362
rnd = random.Random(7)
out = []
add = out.append


def gear(cx, cy, r_out, r_in, teeth):
    pts = []
    for i in range(teeth * 4):
        ang = math.pi * 2 * i / (teeth * 4) - math.pi / 2
        r = r_out if (i % 4) in (1, 2) else r_in
        pts.append(f"{cx + r * math.cos(ang):.1f},{cy + r * math.sin(ang):.1f}")
    return " ".join(pts)


add(f'<svg class="tz" viewBox="0 0 {W} {H}" role="img" aria-label="Plant illustration: Safety, Environment and Utility" xmlns="http://www.w3.org/2000/svg">')
add('''<defs>
<linearGradient id="tz-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#06142b"/><stop offset=".62" stop-color="#0d2f66"/><stop offset="1" stop-color="#1b5bc4"/></linearGradient>
<radialGradient id="tz-glow-a" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffb81c" stop-opacity=".45"/><stop offset="1" stop-color="#ffb81c" stop-opacity="0"/></radialGradient>
<radialGradient id="tz-glow-g" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#3fd49a" stop-opacity=".35"/><stop offset="1" stop-color="#3fd49a" stop-opacity="0"/></radialGradient>
<radialGradient id="tz-glow-c" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#3cc8ff" stop-opacity=".4"/><stop offset="1" stop-color="#3cc8ff" stop-opacity="0"/></radialGradient>
<linearGradient id="tz-shield" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffd35e"/><stop offset="1" stop-color="#f08a00"/></linearGradient>
</defs>''')
add(f'<rect width="{W}" height="{H}" fill="url(#tz-sky)"/>')
# แสงเรืองของแต่ละโซน
add('<ellipse cx="200" cy="230" rx="230" ry="190" fill="url(#tz-glow-a)"/><ellipse cx="600" cy="250" rx="230" ry="170" fill="url(#tz-glow-g)"/><ellipse cx="1000" cy="220" rx="240" ry="190" fill="url(#tz-glow-c)"/>')
# ดาว
for i in range(34):
    x, y = rnd.randint(10, W - 10), rnd.randint(8, 150)
    if 570 < x < 650 and y < 130:
        continue
    add(f'<circle class="tz-twinkle" cx="{x}" cy="{y}" r="{rnd.choice((1, 1, 1.4, 1.8))}" fill="#dff1ff" style="--d:{rnd.uniform(0, 4):.1f}s"/>')
# เมฆลอย
for i, (x, y, s) in enumerate(((60, 70, 1), (520, 40, .8), (880, 90, 1.1))):
    add(f'<g class="tz-cloud" style="--d:{-i * 17}s;--t:{70 + i * 20}s" opacity=".16"><g transform="translate({x} {y}) scale({s})"><ellipse cx="0" cy="0" rx="46" ry="12" fill="#cfe6ff"/><ellipse cx="22" cy="-10" rx="26" ry="12" fill="#cfe6ff"/><ellipse cx="-20" cy="-7" rx="22" ry="9" fill="#cfe6ff"/></g></g>')
# เงาโรงงานด้านหลัง (ต่อเนื่องทั้งแถบ)
roof = f"M0,{GROUND} V300 L60,270 V300 L120,270 V300 L180,270 V300 L240,270 V{GROUND} "
roof += f"M960,{GROUND} V312 L1020,284 V312 L1080,284 V312 L1140,284 V312 L1200,284 V{GROUND} Z"
add(f'<path d="{roof}" fill="#0b2450" opacity=".7"/><rect x="262" y="236" width="14" height="{GROUND - 236}" fill="#0b2450" opacity=".7"/><rect x="292" y="250" width="11" height="{GROUND - 250}" fill="#0b2450" opacity=".7"/>')

# ===== โซนความปลอดภัย (ซ้าย) =====
add('<g class="zone-safety-art">')
add('<circle cx="200" cy="198" r="104" fill="none" stroke="#ffb81c" stroke-width="2" stroke-dasharray="3 14" stroke-linecap="round" class="tz-spin" style="transform-origin:200px 198px;--t:40s"/>')
for d in (0, 1.6):
    add(f'<circle class="tz-ring" cx="200" cy="198" r="84" fill="none" stroke="#ffcf5a" stroke-width="2.5" style="transform-origin:200px 198px;--d:{d}s"/>')
add('<path d="M200,104 L270,132 V204 C270,252 236,284 200,300 C164,284 130,252 130,204 V132 Z" fill="url(#tz-shield)" stroke="#ffe7a3" stroke-width="3" class="tz-shield" style="transform-origin:200px 200px"/>')
add('<path d="M200,122 L254,144 V204 C254,242 228,268 200,282 C172,268 146,242 146,204 V144 Z" fill="#0b2450" opacity=".86"/>')
add('<path class="tz-check" d="M168,204 L192,228 L238,172" fill="none" stroke="#ffffff" stroke-width="14" stroke-linecap="round" stroke-linejoin="round" pathLength="1"/>')
# หอไฟ andon
add('<rect x="357" y="214" width="6" height="148" fill="#0a1d3f"/><rect x="334" y="144" width="52" height="108" rx="12" fill="#06111f" stroke="#254c80" stroke-width="2"/>')
for i, (cy, col) in enumerate(((170, "#ff4d54"), (198, "#ffb81c"), (226, "#34d27f"))):
    add(f'<circle cx="360" cy="{cy}" r="12" fill="#1a2c44"/><circle class="tz-lamp tz-lamp-{i}" cx="360" cy="{cy}" r="12" fill="{col}"/>')
add(f'<rect x="344" y="{GROUND - 10}" width="32" height="10" rx="3" fill="#0a1d3f"/>')
add('</g>')

# ===== โซนสิ่งแวดล้อม (กลาง) =====
add('<g class="zone-env-art">')
add('<circle cx="612" cy="92" r="46" fill="#eaf7ff" opacity=".1"/><circle class="tz-ring" cx="612" cy="92" r="30" fill="none" stroke="#eaf7ff" stroke-width="1.5" style="transform-origin:612px 92px;--d:0s"/><circle cx="612" cy="92" r="24" fill="#eaf7ff" opacity=".92"/><circle cx="604" cy="86" r="24" fill="#9fd8ff" opacity=".2"/>')
add(f'<path d="M400,{GROUND} V312 C450,284 520,300 580,308 C650,318 730,272 800,298 V{GROUND} Z" fill="#0e5a4b"/>')
add(f'<path d="M400,{GROUND} V334 C470,316 540,336 620,330 C700,324 750,318 800,326 V{GROUND} Z" fill="#12755f"/>')
for x, s, d in ((452, 1, 0), (508, .8, -1.2), (566, 1.15, -2.1)):
    base = GROUND - 28
    add(f'<g class="tz-sway" style="transform-origin:{x}px {base}px;--d:{d}s"><rect x="{x - 4}" y="{base - 46 * s:.0f}" width="8" height="{46 * s + 2:.0f}" fill="#0a3a31"/>'
        f'<circle cx="{x}" cy="{base - 62 * s:.0f}" r="{26 * s:.0f}" fill="#25b783"/><circle cx="{x - 16 * s:.0f}" cy="{base - 46 * s:.0f}" r="{18 * s:.0f}" fill="#1e9f72"/><circle cx="{x + 16 * s:.0f}" cy="{base - 48 * s:.0f}" r="{18 * s:.0f}" fill="#34cf98"/></g>')
tx, ty = 716, 196
add(f'<path d="M{tx - 5},{GROUND - 24} L{tx - 2},{ty} H{tx + 2} L{tx + 5},{GROUND - 24} Z" fill="#e9f4ff"/>')
add(f'<g class="tz-spin" style="transform-origin:{tx}px {ty}px;--t:9s">' + "".join(
    f'<path d="M{tx},{ty} C{tx - 6},{ty - 20} {tx - 5},{ty - 52} {tx},{ty - 80} C{tx + 5},{ty - 52} {tx + 6},{ty - 20} {tx},{ty} Z" fill="#f4faff" transform="rotate({a} {tx} {ty})"/>' for a in (0, 120, 240)) + '</g>')
add(f'<circle cx="{tx}" cy="{ty}" r="7" fill="#cfe3f5"/>')
tx2, ty2 = 770, 250
add(f'<path d="M{tx2 - 3},{GROUND - 24} L{tx2 - 1},{ty2} H{tx2 + 1} L{tx2 + 3},{GROUND - 24} Z" fill="#bcd6ec"/>')
add(f'<g class="tz-spin" style="transform-origin:{tx2}px {ty2}px;--t:6.5s">' + "".join(
    f'<path d="M{tx2},{ty2} C{tx2 - 4},{ty2 - 14} {tx2 - 3},{ty2 - 34} {tx2},{ty2 - 52} C{tx2 + 3},{ty2 - 34} {tx2 + 4},{ty2 - 14} {tx2},{ty2} Z" fill="#dcecf9" transform="rotate({a} {tx2} {ty2})"/>' for a in (40, 160, 280)) + '</g>')
for i, x in enumerate((470, 530, 590, 650)):                  # หยดน้ำ
    add(f'<path class="tz-drop" d="M{x},150 C{x - 6},162 {x - 6},170 {x},172 C{x + 6},170 {x + 6},162 {x},150 Z" fill="#7fe0ff" style="--d:{i * .55:.2f}s"/>')
add('<path class="tz-leaf" d="M520,120 C534,108 552,110 556,124 C542,134 526,134 520,120 Z M520,120 L550,124" fill="#5fe0a8" stroke="#0e5a4b" stroke-width="1.2" style="--d:0s"/>')
add('</g>')

# ===== โซน Utility (ขวา) =====
add('<g class="zone-utility-art">')
for px in (886, 1120):
    add(f'<path d="M{px - 14},{GROUND} L{px - 5},232 H{px + 5} L{px + 14},{GROUND} Z" fill="none" stroke="#9fe0ff" stroke-width="3"/>'
        f'<path d="M{px - 9},{GROUND - 40} L{px + 9},{GROUND - 80} M{px + 9},{GROUND - 40} L{px - 9},{GROUND - 80} M{px - 7},{GROUND - 90} L{px + 7},{GROUND - 126} M{px + 7},{GROUND - 90} L{px - 7},{GROUND - 126}" stroke="#9fe0ff" stroke-width="2" fill="none"/>'
        f'<path d="M{px - 42},246 H{px + 42} M{px - 32},272 H{px + 32}" stroke="#9fe0ff" stroke-width="4" stroke-linecap="round"/>')
for y0, y1, sag in ((246, 246, 40), (272, 272, 34)):
    add(f'<path d="M844,{y0} Q1003,{y0 + sag * 2} 1162,{y1}" fill="none" stroke="#2a5d94" stroke-width="2.5"/>'
        f'<path class="tz-flow" d="M844,{y0} Q1003,{y0 + sag * 2} 1162,{y1}" fill="none" stroke="#5ad6ff" stroke-width="3" stroke-linecap="round"/>')
add('<path class="tz-bolt" d="M1012,128 L982,196 H1004 L990,262 L1038,176 H1014 Z" fill="#ffd23f" stroke="#fff2b3" stroke-width="2" stroke-linejoin="round"/>')
add('<circle class="tz-ring" cx="1008" cy="196" r="58" fill="none" stroke="#5ad6ff" stroke-width="2" style="transform-origin:1008px 196px;--d:.4s"/>')
g1, g2 = (1118, 130), (1166, 160)
add(f'<polygon class="tz-spin" points="{gear(g1[0], g1[1], 34, 27, 9)}" fill="#1c6fb5" stroke="#8fd6ff" stroke-width="2" style="transform-origin:{g1[0]}px {g1[1]}px;--t:16s"/><circle cx="{g1[0]}" cy="{g1[1]}" r="9" fill="#0b2450"/>')
add(f'<polygon class="tz-spin tz-rev" points="{gear(g2[0], g2[1], 22, 17, 7)}" fill="#2a8fd8" stroke="#8fd6ff" stroke-width="2" style="transform-origin:{g2[0]}px {g2[1]}px;--t:11s"/><circle cx="{g2[0]}" cy="{g2[1]}" r="6" fill="#0b2450"/>')
add(f'<path d="M800,{GROUND - 8} H1200" stroke="#123a63" stroke-width="14"/><path class="tz-flow tz-pipe" d="M800,{GROUND - 8} H1200" stroke="#4fd0ff" stroke-width="5" fill="none" stroke-linecap="round"/>')
add(f'<g class="tz-spin" style="transform-origin:950px {GROUND - 8}px;--t:5s"><circle cx="950" cy="{GROUND - 8}" r="11" fill="none" stroke="#ffd23f" stroke-width="3"/><path d="M939,{GROUND - 8} H961 M950,{GROUND - 19} V{GROUND + 3}" stroke="#ffd23f" stroke-width="3"/></g>')
for i, x in enumerate((850, 1060, 1090)):
    add(f'<circle class="tz-bubble" cx="{x}" cy="{GROUND - 16}" r="{3 + i}" fill="none" stroke="#bfeaff" stroke-width="1.5" style="--d:{i * 1.1:.1f}s"/>')
add('</g>')

# พื้น + เส้นสีของแต่ละโซน
add(f'<rect x="0" y="{GROUND}" width="{W}" height="{H - GROUND}" fill="#050f21"/>')
add(f'<rect x="0" y="{GROUND}" width="400" height="3" fill="#ffb81c"/><rect x="400" y="{GROUND}" width="400" height="3" fill="#3fd49a"/><rect x="800" y="{GROUND}" width="400" height="3" fill="#3cc8ff"/>')
add('</svg>')

here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(here, "hub", "templates", "_scene.html"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("written", sum(len(x) for x in out), "bytes")
