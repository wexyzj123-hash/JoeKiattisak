/* safety/assets/premium.js — ตัวเลข KPI นับขึ้นครั้งเดียวตอนเปิดหน้า (ค่าจริงอยู่ใน HTML อยู่แล้ว; ปิดเมื่อลดการเคลื่อนไหว) */
(function () {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var nodes = document.querySelectorAll('.premium-stage .kpi-n');
  Array.prototype.forEach.call(nodes, function (el) {
    var text = el.textContent.trim();
    if (!/^\d{1,6}$/.test(text)) return;
    var target = parseInt(text, 10);
    if (target < 2) return;
    var start = null, dur = 900 + Math.min(600, target * 6);
    el.textContent = '0';
    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / dur), eased = 1 - Math.pow(1 - p, 3);
      el.textContent = String(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(step); else el.textContent = text;
    }
    requestAnimationFrame(step);
  });
})();
