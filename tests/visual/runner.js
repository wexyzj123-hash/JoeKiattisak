// tests/visual/runner.js — ตรวจหน้าจริงในเบราว์เซอร์ (Playwright/Chromium): ล้นแนวนอน, สีตัวอักษรที่วัดได้จริง, การสลับธีม
// ใช้: node runner.js < job.json  → พิมพ์ JSON ผลลัพธ์ทาง stdout
const { chromium } = require('playwright');

const MEASURE = () => {
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(parseFloat); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const blend = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 });
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const ratio = (a, b) => { const la = lum(a), lb = lum(b); return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05); };
  const bgOf = (el) => {
    const stack = [];
    for (let n = el; n; n = n.parentElement) {
      const bi = getComputedStyle(n).backgroundImage;
      if (bi && bi.indexOf('gradient') >= 0) { const stops = (bi.match(/rgba?\([^)]+\)/g) || []).map(parse).filter(Boolean); if (stops.length) return { gradient: stops }; }
      const c = parse(getComputedStyle(n).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 0.99) break; } }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    const bodyBg = parse(getComputedStyle(document.body).backgroundColor); if (bodyBg && bodyBg.a > 0) base = bodyBg;
    for (let i = stack.length - 1; i >= 0; i--) base = blend(stack[i], base);
    return base;
  };
  const fails = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    const text = t.nodeValue.trim(); if (!text) continue;
    const el = t.parentElement; if (!el || seen.has(el) || el.closest('svg, script, style, title')) continue;
    seen.add(el);
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const rect = el.getBoundingClientRect(); if (rect.width === 0 || rect.height === 0) continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bgRaw = bgOf(el); const stops = bgRaw.gradient || [bgRaw];                 // พื้นหลังไล่เฉด: วัดกับทุกจุดสี ใช้ค่าที่แย่ที่สุด
    let bg = stops[0], worst = Infinity;
    for (const st of stops) { const f0 = fg.a < 1 ? blend(fg, st) : fg; const rr = ratio(f0, st); if (rr < worst) { worst = rr; bg = st; } }
    const fgc = fg.a < 1 ? blend(fg, bg) : fg;
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight, 10) >= 700;
    const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    const r = ratio(fgc, bg);
    if (r < need) fails.push({ text: text.slice(0, 40), tag: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''), ratio: Math.round(r * 100) / 100, need });
  }
  const vw = document.documentElement.clientWidth;
  const offenders = [];
  document.querySelectorAll('body *').forEach((el) => {
    if (el.closest('svg') ) return;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1) {
      // ข้ามองค์ประกอบที่อยู่ในกล่องที่เลื่อนแนวนอนได้เอง (overflow-x:auto) — ไม่ทำให้หน้าล้น
      let scrolls = false; for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) { const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll') { scrolls = true; break; } }
      if (!scrolls) offenders.push(el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '') + ' right=' + Math.round(r.right));
    }
  });
  const stack = { tables: 0, unlabeled: 0, narrowControls: [], outside: [] };
  document.querySelectorAll('table.table-stack').forEach((t) => {
    stack.tables++;
    t.querySelectorAll('td').forEach((td) => { if (!td.hasAttribute('data-label') && !td.hasAttribute('colspan')) stack.unlabeled++; });
    if (vw <= 640) t.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea, button').forEach((c) => {
      const r = c.getBoundingClientRect(); if (r.width === 0) return;
      const isField = c.tagName !== 'BUTTON';
      if (isField && r.width < 120) stack.narrowControls.push((c.name || c.tagName) + '=' + Math.round(r.width));
      if (r.right > vw + 1 || r.left < -1) stack.outside.push((c.name || c.tagName) + '@' + Math.round(r.right));
    });
  });
  return { stack, scrollWidth: document.documentElement.scrollWidth, clientWidth: vw, innerWidth: window.innerWidth, offenders: offenders.slice(0, 6), contrastFails: fails.slice(0, 12), title: document.title };
};

(async () => {
  const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
  const browser = await chromium.launch();
  const out = { pages: [], errors: [] };
  try {
    if (job.mode === 'labels') {
      const page = await browser.newPage();
      await page.setContent(job.html);
      await page.addScriptTag({ path: job.script });
      out.pages.push(await page.evaluate(() => Array.from(document.querySelectorAll('table.table-stack')).map((t) => Array.from(t.querySelectorAll('td')).map((td) => td.getAttribute('data-label')))));
      out.hrows = await page.evaluate(() => document.querySelectorAll('tr.hrow').length);
    } else if (job.mode === 'toggle') {
      for (const variant of ['normal', 'blocked']) {
        const ctx = await browser.newContext({ viewport: { width: 1000, height: 700 }, colorScheme: job.system || 'dark' });
        const page = await ctx.newPage();
        const errs = []; page.on('pageerror', (e) => errs.push(String(e)));
        if (variant === 'blocked') await page.addInitScript(() => { Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } }); });
        await page.goto(job.url);
        const attr = () => page.evaluate(() => document.documentElement.getAttribute('data-theme'));
        const before = await attr();
        await page.click('[data-theme-toggle]');
        const after = await attr();
        await page.reload();
        const afterReload = await attr();
        out.pages.push({ variant, before, after, afterReload, errors: errs });
        await ctx.close();
      }
    } else {
      for (const theme of job.themes) {
        for (const vp of job.viewports) {
          const ctx = await browser.newContext({ viewport: vp, colorScheme: theme });
          await ctx.addInitScript((t) => { try { localStorage.setItem('aiat-theme', t); } catch (e) {} }, theme);
          for (const p of job.pages) {
            if (p.cookie) await ctx.addCookies([p.cookie]);
            const page = await ctx.newPage();
            const errs = []; page.on('pageerror', (e) => errs.push(String(e)));
            const resp = await page.goto(p.url, { waitUntil: 'load' });
            if (p.waitMs) await page.waitForTimeout(p.waitMs);        // รอแอนิเมชันจบก่อนวัดสี/ถ่ายภาพ
            const m = await page.evaluate(MEASURE);
            if (job.shotDir) await page.screenshot({ path: `${job.shotDir}/${p.name}-${theme}-${vp.width}.png`, fullPage: true });
            out.pages.push({ name: p.name, theme, width: vp.width, status: resp ? resp.status() : 0, pageErrors: errs, ...m });
            await page.close();
          }
          await ctx.close();
        }
      }
    }
  } finally { await browser.close(); }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(2); });
