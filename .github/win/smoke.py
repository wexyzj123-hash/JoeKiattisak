"""ทดสอบ E-Checksheet บน Windows จริง (GitHub Actions windows-latest) — เปิดเซิร์ฟเวอร์จริงแล้วใช้ Microsoft Edge ของเครื่องกดดูทุกหน้า

ตรวจ: หน้าเปิดได้ (HTTP 200) · ไม่มี JavaScript error · ไม่ล้นจอ (1366 / 390 px) · ส่วนสำคัญของหน้าภาพรวม ·
      รายงานสรุปเวลาตรวจเป็น PDF (สร้างด้วย Edge ฝั่งเซิร์ฟเวอร์ — แบบเดียวกับเครื่องจริงที่โรงงาน) · Excel
ผล: พิมพ์ PASS/FAIL ทีละข้อ + เขียนตารางสรุปลง GITHUB_STEP_SUMMARY · มีข้อล้ม = exit 1
ใช้: python smoke.py <base_url> <out_dir>"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):      # คอนโซล Windows (cp1252) พิมพ์ภาษาไทยไม่ได้ — แบบเดียวกับ run.py
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
B = sys.argv[1].rstrip("/")
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
R: list[tuple[bool, str, str]] = []
T = date.today()


def ok(name: str, cond: bool, extra="") -> None:
    R.append((bool(cond), name, str(extra)[:200]))
    print(("PASS " if cond else "FAIL ") + name, extra if not cond else "", flush=True)


PAGES = ["/dashboard", f"/dashboard?date={(T - timedelta(days=1)).isoformat()}", "/rounds", "/submissions", "/alerts", "/actions",
         "/equipment", "/forms", "/trends", "/quality", "/reports", "/reports/daily", "/signoffs", "/approvals",
         "/analytics/inspection-time", "/analytics/inspection-time?factory=1", "/analytics/inspection-time/report",
         "/team/scorecard", "/admin/users", "/admin/health", "/admin/layout", "/energy/", "/tv", "/today"]
CHECK = """()=>{const iw=innerWidth;const m=document.querySelector('main')||document.body;
 const inScroll=(e)=>{for(let a=e.parentElement;a&&a!==m;a=a.parentElement){const o=getComputedStyle(a).overflowX;if(o!=='visible')return true}return false};
 return {ovf: document.documentElement.scrollWidth>iw+1 ? document.documentElement.scrollWidth : 0,
   wide: [...m.querySelectorAll('*')].filter(e=>{const b=e.getBoundingClientRect();return b.width>0&&b.right>iw+2&&!inScroll(e)&&getComputedStyle(e).position!=='fixed'}).slice(0,3).map(e=>e.tagName+'.'+String(e.className.baseVal??e.className).slice(0,40))}}"""


def login(pg) -> None:
    pg.goto(B + "/login")
    pg.fill("input[name=username]", "admin")
    pg.fill("input[name=password]", "Demo#1234")
    pg.click("button[type=submit]")
    pg.wait_for_load_state("networkidle")


with sync_playwright() as p:
    if os.name == "nt":
        b = p.chromium.launch(channel="msedge")                   # Microsoft Edge ที่ติดตั้งมากับ Windows
    else:                                                         # ลองบนเครื่องพัฒนา (Linux) ด้วย Chromium
        import glob
        b = p.chromium.launch(executable_path=(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome") or [None])[0])
    ok("เปิด Microsoft Edge ได้", True, b.version)
    for W, mobile in ((1366, False), (390, True)):
        ctx = b.new_context(viewport={"width": W, "height": 900}, is_mobile=mobile, has_touch=mobile)
        ctx.add_init_script("const _g=Storage.prototype.getItem;Storage.prototype.getItem=function(k){return String(k).startsWith('brief_seen:')?'1':_g.call(this,k)}")
        pg = ctx.new_page()
        errs: list[str] = []
        pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)[:150]))
        login(pg)
        ok(f"[{W}px] เข้าสู่ระบบ", "/login" not in pg.url, pg.url)
        for u in PAGES:
            errs.clear()
            r = pg.goto(B + u, wait_until="load")
            pg.wait_for_timeout(700)
            c = pg.evaluate(CHECK)
            exp_ovf = u in ("/tv",) and mobile                          # หน้าทีวีออกแบบสำหรับจอใหญ่
            ok(f"[{W}px] {u} เปิดได้", r and r.status == 200, r.status if r else 0)
            ok(f"[{W}px] {u} ไม่มี JS error", not errs, errs[:2])
            ok(f"[{W}px] {u} ไม่ล้นจอ", exp_ovf or (not c["ovf"] and not c["wide"]), c)
        if W == 1366:
            pg.goto(B + "/dashboard"); pg.wait_for_timeout(1500)
            ok("ภาพรวม: แถบตัวเลข 7 ช่อง", pg.locator("#ov-today .kx").count() == 7, pg.locator("#ov-today .kx").count())
            ok("ภาพรวม: ช่องเวลาตรวจรวม", pg.locator(".kx-it").count() == 1 and "เวลาตรวจรวม" in pg.inner_text(".kx-it"))
            ok("ภาพรวม: ต้องจัดการตอนนี้ + สถานะการแก้ไข", pg.locator("#todo").count() == 1 and (pg.locator(".fx-sum").count() == 1 or pg.locator("#todo .empty").count() == 1))
            pg.screenshot(path=os.path.join(OUT, "dashboard_1366.png"), full_page=False)
            pg.locator(".kx-it").click(); pg.wait_for_timeout(900)
            ok("ภาพรวม: กดช่องเวลาตรวจ → แผงดูต่อ", pg.locator(".od").is_visible() and "เวลาตรวจรวม" in pg.inner_text(".od"))
            q = f"from={(T - timedelta(days=29)).isoformat()}&to={T.isoformat()}"
            res = ctx.request.get(B + f"/analytics/inspection-time/report?{q}&format=pdf", timeout=240000, max_redirects=0)
            body = res.body()
            ok("รายงานสรุปเวลาตรวจ: PDF (Edge ฝั่งเซิร์ฟเวอร์)", res.status == 200 and res.headers.get("content-type", "").startswith("application/pdf") and body[:4] == b"%PDF",
               (res.status, res.headers.get("content-type"), len(body)))
            if body[:4] == b"%PDF":
                with open(os.path.join(OUT, "inspection_time_report.pdf"), "wb") as fh:
                    fh.write(body)
            res = ctx.request.get(B + f"/analytics/inspection-time.xlsx?{q}")
            ok("เวลาตรวจ: Excel", res.status == 200 and res.body()[:2] == b"PK", res.status)
            res = ctx.request.get(B + f"/reports/daily?date={T.isoformat()}&format=pdf", timeout=240000, max_redirects=0)
            ok("รายงานประจำวัน: PDF", res.status == 200 and res.body()[:4] == b"%PDF", (res.status, res.headers.get("content-type")))
        else:
            pg.goto(B + "/dashboard"); pg.wait_for_timeout(1500)
            pg.screenshot(path=os.path.join(OUT, "dashboard_390.png"), full_page=False)
        ctx.close()
    b.close()

n_ok = sum(1 for x in R if x[0])
print(f"\nสรุป {n_ok} / {len(R)}")
summ = os.environ.get("GITHUB_STEP_SUMMARY")
if summ:
    with open(summ, "a", encoding="utf-8") as fh:
        fh.write(f"### ทดสอบบน Windows ด้วย Microsoft Edge: ผ่าน {n_ok} / {len(R)}\n\n")
        bad = [x for x in R if not x[0]]
        if bad:
            fh.write("| ข้อที่ไม่ผ่าน | รายละเอียด |\n|---|---|\n" + "".join(f"| {n} | {e} |\n" for _, n, e in bad))
sys.exit(0 if n_ok == len(R) else 1)
