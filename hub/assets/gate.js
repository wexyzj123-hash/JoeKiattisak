/* hub/assets/gate.js — หน้าเลือกระบบ: ฮีโร่และการ์ดเอียงตามเมาส์เบา ๆ ให้รู้สึกมีมิติ (ตกแต่งอย่างเดียว; ปิดเมื่อลดการเคลื่อนไหวหรือเป็นจอสัมผัส) */
(function () {
  if (window.matchMedia && (window.matchMedia('(prefers-reduced-motion: reduce)').matches || window.matchMedia('(hover: none)').matches)) return;
  var stage = document.getElementById('gate-stage');
  if (!stage) return;
  var hero = document.getElementById('gate-hero');
  var tiles = Array.prototype.slice.call(stage.querySelectorAll('.gate .tile'));
  var raf = 0, px = 0, py = 0, over = null, tx = 0, ty = 0;
  function paint() {
    raf = 0;
    if (hero) { hero.style.setProperty('--rx', (py * -3).toFixed(2) + 'deg'); hero.style.setProperty('--ry', (px * 4).toFixed(2) + 'deg'); }
    tiles.forEach(function (t) {
      if (t === over) { t.style.setProperty('--rx', (ty * -8).toFixed(2) + 'deg'); t.style.setProperty('--ry', (tx * 10).toFixed(2) + 'deg'); }
      else { t.style.setProperty('--rx', '0deg'); t.style.setProperty('--ry', '0deg'); }
    });
  }
  stage.addEventListener('pointermove', function (e) {
    var s = stage.getBoundingClientRect();
    px = Math.max(-1, Math.min(1, (e.clientX - (s.left + s.width / 2)) / (s.width / 2)));
    py = Math.max(-1, Math.min(1, (e.clientY - (s.top + 260)) / 400));
    over = null;
    tiles.forEach(function (t) {
      var r = t.getBoundingClientRect();
      if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
        over = t; tx = ((e.clientX - r.left) / r.width) * 2 - 1; ty = ((e.clientY - r.top) / r.height) * 2 - 1;
      }
    });
    if (!raf) raf = requestAnimationFrame(paint);
  });
  stage.addEventListener('pointerleave', function () { px = py = 0; over = null; if (!raf) raf = requestAnimationFrame(paint); });
})();
