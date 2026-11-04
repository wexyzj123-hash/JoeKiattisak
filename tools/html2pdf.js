// tools/html2pdf.js <input.html> <output.pdf> "<ข้อความท้ายกระดาษ>" — พิมพ์ HTML เป็น PDF ด้วย Chromium (ใช้กับคู่มือ)
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const [src, out, footer] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('file://' + path.resolve(src), { waitUntil: 'networkidle' });
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({ path: out, preferCSSPageSize: true, printBackground: true, displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: `<div style="width:100%;font-size:8px;color:#5b6878;padding:0 17mm;display:flex;justify-content:space-between;font-family:Loma,sans-serif"><span>${footer}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>` });
  await b.close();
})();
