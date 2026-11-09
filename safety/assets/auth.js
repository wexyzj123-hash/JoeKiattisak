/* hub/assets/login.js — การ์ดเข้าสู่ระบบเอียงเบา ๆ ตามเมาส์ + แสงวิ่งตามเคอร์เซอร์ (ตกแต่งอย่างเดียว; ปิดเมื่อลดการเคลื่อนไหวหรือเป็นหน้าจอสัมผัส) */
(function () {
  var stage = document.getElementById('auth-stage');
  var card = stage && stage.querySelector('.login-card');
  if (!card) return;
  if (window.matchMedia && (window.matchMedia('(prefers-reduced-motion: reduce)').matches || window.matchMedia('(hover: none)').matches)) return;
  var raf = 0, nx = 0, ny = 0, mx = 50, my = 0;
  function paint() {
    raf = 0;
    card.style.setProperty('--rx', (ny * -5).toFixed(2) + 'deg');
    card.style.setProperty('--ry', (nx * 6).toFixed(2) + 'deg');
    card.style.setProperty('--mx', mx.toFixed(0) + '%');
    card.style.setProperty('--my', my.toFixed(0) + '%');
  }
  stage.addEventListener('pointermove', function (e) {
    var s = stage.getBoundingClientRect(), r = card.getBoundingClientRect();
    nx = Math.max(-1, Math.min(1, (e.clientX - (s.left + s.width / 2)) / (s.width / 2)));
    ny = Math.max(-1, Math.min(1, (e.clientY - (s.top + s.height / 2)) / (s.height / 2)));
    mx = ((e.clientX - r.left) / r.width) * 100; my = ((e.clientY - r.top) / r.height) * 100;
    if (!raf) raf = requestAnimationFrame(paint);
  });
  stage.addEventListener('pointerleave', function () { nx = ny = 0; if (!raf) raf = requestAnimationFrame(paint); });
})();
