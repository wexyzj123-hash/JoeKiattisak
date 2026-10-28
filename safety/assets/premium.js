/* safety/assets/premium.js — หน้าภาพรวม: KPI นับขึ้น, กราฟแนวโน้มโต้ตอบ (ช่วง 3/6/12 เดือน, ทูลทิป, คลิกเดือน), ลิ้นชักรายละเอียด (drawer)
   ทุกอย่างเสริมจาก HTML ที่ใช้งานได้อยู่แล้ว (ลิงก์ปกติ/ตัวเลขจริง) — ปิดแอนิเมชันเมื่อผู้ใช้ตั้งค่าลดการเคลื่อนไหว */
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ----- ตัวเลข KPI นับขึ้นครั้งเดียว ----- */
  if (!reduce) {
    Array.prototype.forEach.call(document.querySelectorAll('.premium-stage .kpi-n'), function (el) {
      var text = el.textContent.trim();
      if (!/^\d{1,6}$/.test(text)) return;
      var target = parseInt(text, 10);
      if (target < 2) return;
      var start = null, dur = 900 + Math.min(600, target * 6);
      el.textContent = '0';
      (function step(ts) {
        if (start === null) start = ts;
        var p = Math.min(1, (ts - start) / dur);
        el.textContent = String(Math.round(target * (1 - Math.pow(1 - p, 3))));
        if (p < 1) requestAnimationFrame(step); else el.textContent = text;
      })(performance.now());
    });
  }

  /* ----- กราฟแนวโน้ม ----- */
  var box = document.getElementById('trend-chart'), dataEl = document.getElementById('trend-data');
  var MONTHS = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
  if (box && dataEl) {
    var all = []; try { all = JSON.parse(dataEl.textContent); } catch (e) {}
    var canList = !!document.querySelector('a.kpi[href^="/hazards"]');
    var range = 12, NS = 'http://www.w3.org/2000/svg';
    var tip = document.createElement('div'); tip.className = 'chart-tip'; tip.setAttribute('aria-hidden', 'true'); box.appendChild(tip);
    var W = 720, H = 270, L = 38, R = 14, T = 14, B = 32;
    var el = function (n, a, p) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
    var smooth = function (pts) {
      if (pts.length < 2) return 'M' + pts[0].join(',');
      var d = 'M' + pts[0].join(',');
      for (var i = 0; i < pts.length - 1; i++) {
        var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
        d += ' C' + [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6, p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1]].map(function (v) { return v.toFixed(1); }).join(',');
      }
      return d;
    };
    var draw = function (animate) {
      var rows = all.slice(-range), n = rows.length;
      var old = box.querySelector('svg.main'); if (old) old.remove();
      if (!n) { box.setAttribute('data-state', 'empty'); return; }
      var peak = Math.max(4, Math.max.apply(null, rows.map(function (r) { return Math.max(r.reported, r.closed); })));
      peak = Math.ceil(peak / 4) * 4;
      var svg = el('svg', { 'class': 'main', viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'กราฟแนวโน้มแจ้งและปิด ' + n + ' เดือนล่าสุด' });
      var defs = el('defs', {}, svg), g = el('linearGradient', { id: 'ch-area', x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      el('stop', { offset: 0, 'stop-color': '#2a74e8', 'stop-opacity': .28 }, g); el('stop', { offset: 1, 'stop-color': '#2a74e8', 'stop-opacity': 0 }, g);
      var x = function (i) { return L + (W - L - R) * (n === 1 ? .5 : i / (n - 1)); }, y = function (v) { return T + (H - T - B) * (1 - v / peak); };
      for (var t = 0; t <= 4; t++) { var gv = peak * t / 4; el('line', { 'class': 'ch-grid', x1: L, x2: W - R, y1: y(gv), y2: y(gv) }, svg); var tx = el('text', { 'class': 'ch-axis', x: L - 8, y: y(gv) + 4, 'text-anchor': 'end' }, svg); tx.textContent = Math.round(gv); }
      rows.forEach(function (r, i) { var lab = el('text', { 'class': 'ch-axis', x: x(i), y: H - 10, 'text-anchor': 'middle' }, svg); lab.textContent = MONTHS[parseInt(r.m.slice(5), 10) - 1]; });
      var pr = rows.map(function (r, i) { return [x(i), y(r.reported)]; }), pc = rows.map(function (r, i) { return [x(i), y(r.closed)]; });
      el('path', { d: smooth(pr) + ' L' + x(n - 1) + ',' + y(0) + ' L' + x(0) + ',' + y(0) + ' Z', fill: 'url(#ch-area)' }, svg);
      var l1 = el('path', { 'class': 'ch-line' + (animate ? ' drawing' : ''), d: smooth(pr), stroke: '#2a74e8', pathLength: 1 }, svg);
      var l2 = el('path', { 'class': 'ch-line' + (animate ? ' drawing' : ''), d: smooth(pc), stroke: '#1fa57a', pathLength: 1 }, svg);
      var hover = el('line', { 'class': 'ch-hover', y1: T, y2: H - B }, svg), d1 = el('circle', { 'class': 'ch-dot', r: 5.5, fill: '#2a74e8', stroke: '#fff', 'stroke-width': 2 }, svg), d2 = el('circle', { 'class': 'ch-dot', r: 5.5, fill: '#1fa57a', stroke: '#fff', 'stroke-width': 2 }, svg);
      var colw = (W - L - R) / Math.max(1, n - 1);
      rows.forEach(function (r, i) {
        var parent = svg;
        if (canList) { parent = el('a', { href: '/hazards?month=' + r.m, 'aria-label': 'ดูรายการที่แจ้งใน ' + MONTHS[parseInt(r.m.slice(5), 10) - 1] + ' ' + r.reported + ' รายการ' }, svg); }
        var hit = el('rect', { 'class': 'ch-hit', x: x(i) - colw / 2, y: T, width: colw, height: H - T - B, tabindex: canList ? null : 0 }, parent);
        var show = function () {
          hover.setAttribute('x1', x(i)); hover.setAttribute('x2', x(i)); hover.style.opacity = 1;
          d1.setAttribute('cx', x(i)); d1.setAttribute('cy', y(r.reported)); d2.setAttribute('cx', x(i)); d2.setAttribute('cy', y(r.closed)); d1.style.opacity = d2.style.opacity = 1;
          tip.innerHTML = '<b>' + MONTHS[parseInt(r.m.slice(5), 10) - 1] + ' ' + r.m.slice(0, 4) + '</b><span>แจ้งใหม่ <strong>' + r.reported + '</strong></span><span>ปิดแล้ว <strong>' + r.closed + '</strong></span>' + (r.accidents || r.near_miss ? '<span>อุบัติเหตุ/Near Miss <strong>' + r.accidents + '/' + r.near_miss + '</strong></span>' : '');
          var rect = box.getBoundingClientRect(), k = rect.width / W; tip.style.left = Math.min(rect.width - 150, Math.max(0, x(i) * k - 65)) + 'px'; tip.style.top = Math.max(0, y(Math.max(r.reported, r.closed)) * k - 74) + 'px'; tip.classList.add('show');
        };
        var hide = function () { hover.style.opacity = d1.style.opacity = d2.style.opacity = 0; tip.classList.remove('show'); };
        hit.addEventListener('mouseenter', show); hit.addEventListener('focus', show); hit.addEventListener('mouseleave', hide); hit.addEventListener('blur', hide);
      });
      box.appendChild(svg); box.classList.add('js');
      if (animate) requestAnimationFrame(function () { requestAnimationFrame(function () { l1.classList.remove('drawing'); l2.classList.remove('drawing'); }); });
    };
    draw(!reduce);
    Array.prototype.forEach.call(document.querySelectorAll('.seg button[data-range]'), function (b) {
      b.addEventListener('click', function () {
        range = parseInt(b.getAttribute('data-range'), 10);
        Array.prototype.forEach.call(b.parentNode.children, function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        draw(!reduce);
      });
    });
  }

  /* ----- ลิ้นชักรายละเอียด ----- */
  var root = document.getElementById('drawer-root');
  if (!root) return;
  var panel = root.querySelector('.drawer'), content = root.querySelector('.drawer-content'), lastFocus = null, token = 0;
  function close() {
    root.classList.remove('open');
    setTimeout(function () { root.hidden = true; if (lastFocus && lastFocus.focus) lastFocus.focus(); }, reduce ? 0 : 280);
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    var f = panel.querySelectorAll('a[href], button:not([disabled])'); if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  function open(ref, from) {
    var parts = ref.split(':'); if (parts.length !== 2 || !/^\w+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) return false;
    lastFocus = from || document.activeElement; var mine = ++token;
    content.innerHTML = '<p class="drawer-sub">กำลังโหลด…</p>'; root.hidden = false;
    requestAnimationFrame(function () { root.classList.add('open'); panel.focus(); });
    document.addEventListener('keydown', onKey);
    fetch('/drawer/' + parts[0] + '/' + parts[1], { credentials: 'same-origin', headers: { 'X-Requested-With': 'drawer' } })
      .then(function (r) { return r.text().then(function (t) { return { ok: r.ok, t: t }; }); })
      .then(function (r) { if (mine === token) { content.innerHTML = r.t; var h = content.querySelector('#drawer-title'); if (h) { h.setAttribute('tabindex', '-1'); } } })
      .catch(function () { if (mine === token) content.innerHTML = '<p class="drawer-sub">โหลดรายละเอียดไม่สำเร็จ — ลองใหม่อีกครั้ง หรือเปิดหน้ารายละเอียดจากลิงก์</p>'; });
    return true;
  }
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-drawer-close]')) { close(); return; }
    var a = e.target.closest('a[data-drawer]');
    if (a && !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button)) { if (open(a.getAttribute('data-drawer'), a)) e.preventDefault(); return; }
    var row = e.target.closest('tr[data-drawer-row]');
    if (row && !e.target.closest('a, button, input, select, textarea')) open(row.getAttribute('data-drawer-row'), row.querySelector('a'));
  });
})();
