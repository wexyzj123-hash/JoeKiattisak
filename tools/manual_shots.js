// tools/manual_shots.js — จับภาพหน้าจอจริงสำหรับคู่มือ (อ่านงานจาก stdin ที่ tools/manual_world.py ส่งมา)
const { chromium } = require('playwright');
const path = require('path');

let raw = '';
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', async () => {
  const job = JSON.parse(raw);
  const browser = await chromium.launch();
  const ids = job.ids;
  const jobs = [];
  const add = (name, o) => jobs.push({ name, ...o });

  // ---- Hub ----
  add('hub-login', { app: 'hub', who: null, url: '/login' });
  add('hub-launcher', { app: 'hub', who: 'hub', url: '/' });
  add('hub-launcher-public', { app: 'hub', who: null, url: '/' });
  add('hub-register', { app: 'hub', who: null, url: '/register', full: true, fill: { username: 'somying.k', name_th: 'สมหญิง ใจดี', factory_id: { label: /บางนา/ }, department_id: { label: /ผลิต/ }, line_id: { label: /ประกอบ/ } } });
  add('hub-users', { app: 'hub', who: 'hub', url: '/admin/users', full: true });
  add('hub-apps', { app: 'hub', who: 'hub', url: '/admin/apps', full: true });
  // ---- ผู้แจ้ง ----
  add('home-reporter', { who: 'reporter', url: '/' });
  add('hazard-list', { who: 'reporter', url: '/hazards' });
  add('hazard-form', { who: 'reporter', url: '/hazards/new', full: true });
  add('hazard-form-filled', { who: 'reporter', url: '/hazards/new', full: true, fill: {
    title: 'ถังสารเคมีวางโล่ง ไม่มีฝาปิด', description: 'ถังโซเดียมไฮดรอกไซด์ 20 ลิตร วางข้างทางเดินในคลัง ไม่มีฝาปิดและไม่มีป้ายบอกชนิดสารเคมี',
    risk_area_id: { label: /คลังสารเคมี/ }, hazard_type_id: { label: /สารเคมี/ }, source_id: { label: /พนักงานแจ้ง/ } } });
  add('hazard-detail-mine', { who: 'reporter', url: `/hazards/${ids.h1}`, full: true });
  add('hazard-rejected', { who: 'reporter', url: `/hazards/${ids.h5}`, full: true });
  add('incident-form', { who: 'reporter', url: '/incidents/new', full: true });
  add('incident-detail', { who: 'reporter', url: `/incidents/${ids.inc1}`, full: true });
  add('notifications', { who: 'reporter', url: '/notifications' });
  add('profile', { who: 'reporter', url: '/profile' });
  add('hazard-form-mobile', { who: 'reporter', url: '/hazards/new', w: 390, h: 844, full: true });
  add('home-mobile', { who: 'reporter', url: '/', w: 390, h: 844 });
  // ---- เจ้าของงาน / หัวหน้า ----
  add('capa-owner', { who: 'head', url: `/capas/${ids.cp1}`, full: true });
  add('capa-list', { who: 'head', url: '/capas' });
  add('hazard-owner-close', { who: 'head', url: `/hazards/${ids.h4}`, full: true });
  // ---- จป. / ผู้ประเมิน ----
  add('hazard-assess', { who: 'assessor', url: `/hazards/${ids.h2}`, full: true });
  add('hazard-assign', { who: 'assessor', url: `/hazards/${ids.h3}`, full: true });
  add('incident-investigate', { who: 'assessor', url: `/incidents/${ids.inc2}`, full: true });
  add('patrol-list', { who: 'assessor', url: '/patrols' });
  add('patrol-round', { who: 'assessor', url: `/patrols/${ids.draft}`, full: true });
  add('patrol-round-done', { who: 'assessor', url: `/patrols/${ids.round}`, full: true });
  add('capa-detail-verify', { who: 'assessor', url: `/capas/${ids.cp2}`, full: true });
  add('reports-index', { who: 'assessor', url: '/reports' });
  add('report-monthly', { who: 'assessor', url: '/reports/monthly', full: true });
  add('incident-stats', { who: 'assessor', url: '/incidents/stats', full: true });
  // ---- ผู้ดูแลระบบ ----
  add('overview', { who: 'admin', url: '/overview', full: true });
  add('overview-dept', { who: 'admin', url: '/overview/departments', full: true });
  add('home-admin', { who: 'admin', url: '/', full: true });
  add('drawer', { who: 'admin', url: '/', click: 'tr[data-drawer-row] td', waitMs: 900 });
  add('admin-roles', { who: 'admin', url: '/admin/roles', full: true });
  add('admin-users', { who: 'admin', url: '/admin/users', full: true });
  add('master-risk-areas', { who: 'admin', url: '/master/risk-areas', full: true });
  add('master-risk-area-new', { who: 'admin', url: '/master/risk-areas/new', full: true });
  add('master-hazard-types', { who: 'admin', url: '/master/hazard-types' });
  add('admin-criteria', { who: 'admin', url: '/admin/criteria', full: true });
  add('admin-custom-fields', { who: 'admin', url: '/admin/custom-fields', full: true });
  add('admin-injury', { who: 'admin', url: '/admin/injury-severities', full: true });
  add('admin-checklists', { who: 'admin', url: '/admin/checklists', full: true });
  add('admin-checklist-items', { who: 'admin', url: `/admin/checklists/${ids.checklist}`, full: true });
  add('admin-plans', { who: 'admin', url: '/admin/patrol-plans', full: true });
  add('admin-capa-settings', { who: 'admin', url: '/admin/capa-settings', full: true });
  add('admin-report-due', { who: 'admin', url: '/admin/report-due', full: true });
  add('admin-notifications', { who: 'admin', url: '/admin/notifications', full: true });
  add('search', { who: 'admin', url: '/search?q=น้ำมัน' });

  const log = [];
  for (const j of jobs) {
    const base = j.app === 'hub' ? job.hub : job.safety;
    const ctx = await browser.newContext({ viewport: { width: j.w || 1280, height: j.h || 800 }, deviceScaleFactor: 1, locale: 'th-TH', colorScheme: 'light' });
    if (j.who === 'hub') await ctx.addCookies([job.hub_cookie]);
    else if (j.who) await ctx.addCookies([job.cookies[j.who]]);
    const page = await ctx.newPage();
    try {
      const resp = await page.goto(base + j.url, { waitUntil: 'networkidle' });
      if (j.fill) {
        for (const [k, v] of Object.entries(j.fill)) {
          const el = page.locator(`[name="${k}"]`);
          if (typeof v === 'string') await el.fill(v);
          else { const opts = await el.locator('option').allTextContents(); const hit = opts.find((t) => v.label.test(t)); await el.selectOption({ label: hit }); }
        }
      }
      if (j.click) { await page.locator(j.click).first().click(); }
      await page.waitForTimeout(j.waitMs || 1600);
      const file = path.join(job.out, j.name + '.png');
      await page.screenshot({ path: file, fullPage: !!j.full });
      log.push(`${resp.status()} ${j.name}`);
    } catch (e) { log.push(`ERR ${j.name} ${String(e).slice(0, 120)}`); }
    await ctx.close();
  }
  await browser.close();
  console.log(log.join('\n'));
});
