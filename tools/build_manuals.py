"""สร้างคู่มือ PDF จาก docs/manual/*.html: ตัดต่อภาพหน้าจอ (จาก tools/manual_world.py) แล้วพิมพ์ด้วย Chromium
ใช้: python tools/build_manuals.py <โฟลเดอร์ภาพหน้าจอ>   → docs/manual/*.pdf"""
import os
import shutil
import subprocess
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAN = os.path.join(ROOT, "docs", "manual")
SIDEBAR, TOPBAR = 264, 66

# ชื่อภาพ → (โหมด, ความสูงสูงสุด) ; body = ตัดแถบเมนู+แถบบนออก, full = ทั้งหน้า, top = ครึ่งบนของเนื้อหา
CROPS = {
    "home-reporter": ("full", None), "home-mobile": ("full", None), "hazard-form-mobile": ("full", None), "hub-login": ("full", None), "hub-register": ("full", None), "hub-launcher": ("full", None),
    "drawer": ("full", None), "home-admin": ("body", 1500),
}
BODY_MAX = {"admin-criteria": 1500, "hazard-form": 640, "hazard-form-filled": 760, "notifications": 560, "profile": 560, "hazard-list": 620, "search": 520, "capa-list": 620, "patrol-list": 640,
            "reports-index": 600, "master-hazard-types": 600}


def trim_bottom(im: Image.Image) -> Image.Image:
    """ตัดพื้นที่ว่างท้ายหน้า (สีพื้นหลังล้วน) ออก เหลือขอบล่างเล็กน้อย"""
    bg = im.getpixel((8, im.height - 8))
    px = im.load()
    for y in range(im.height - 1, 0, -1):
        if any(px[x, y] != bg for x in range(0, im.width, 7)):
            return im.crop((0, 0, im.width, min(im.height, y + 28)))
    return im


def prep(shots: str) -> None:
    out = os.path.join(MAN, "img")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for fn in sorted(os.listdir(shots)):
        if not fn.endswith(".png") or fn.startswith(("sf-", "hub-admin", "hub-launcher-", "hub-login-")):
            continue
        name = fn[:-4]
        im = Image.open(os.path.join(shots, fn)).convert("RGB")
        mode, cap = CROPS.get(name, ("body", BODY_MAX.get(name)))
        if mode == "body":
            im = im.crop((SIDEBAR, TOPBAR if im.width > 800 else 0, im.width, im.height))
            im = trim_bottom(im)
            if im.height > (cap or 1150):
                im = im.crop((0, 0, im.width, cap or 1150))
        w = 1000 if im.width > 1000 else im.width
        if im.width != w:
            im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        im.quantize(colors=200, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(os.path.join(out, name + ".png"), optimize=True)


def main(shots: str) -> int:
    prep(shots)
    env = dict(os.environ)
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    env["NODE_PATH"] = os.pathsep.join(p for p in (root, "/usr/lib/node_modules", "/usr/local/lib/node_modules") if p)
    for src, out, foot in (("user.html", "คู่มือผู้ใช้-ผู้แจ้ง.pdf", "AIAT Safety · คู่มือผู้ใช้งาน (ผู้แจ้งและเจ้าของงาน)"),
                           ("admin.html", "คู่มือผู้ดูแลระบบ.pdf", "AIAT Safety · คู่มือผู้ดูแลระบบ")):
        r = subprocess.run(["node", os.path.join(ROOT, "tools", "html2pdf.js"), os.path.join(MAN, src), os.path.join(MAN, out), foot], env=env)
        if r.returncode:
            return r.returncode
        print(out, os.path.getsize(os.path.join(MAN, out)) // 1024, "KB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
