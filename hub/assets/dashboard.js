/* hub/assets/dashboard.js — นาฬิกา, จังหวะเปิดหน้า (ตัวเลขพลิก/ไฟ andon/เส้นกราฟ) ครั้งเดียว, และรีเฟรชตัวเลขทุก 60 วินาทีโดยไม่เล่นซ้ำ */
(function () {
  var board = document.getElementById('board');
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var clock = document.getElementById('clock');
  function tick() {
    if (!clock) return;
    try {
      clock.textContent = new Intl.DateTimeFormat('th-TH-u-ca-buddhist', { timeZone: clock.dataset.tz || 'Asia/Bangkok', weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
    } catch (e) { clock.textContent = new Date().toLocaleString(); }
  }
  tick(); setInterval(tick, 1000);

  if (!board) return;
  if (!reduce) {                                   // เริ่มจากสถานะว่าง แล้วปล่อยให้ transition วิ่งไปสถานะจริง
    board.classList.add('pre');
    var zone = board.querySelector('.zone-safety'); if (zone) zone.classList.add('boot');
    requestAnimationFrame(function () { requestAnimationFrame(function () { board.classList.remove('pre'); }); });
    setTimeout(function () { if (zone) zone.classList.remove('boot'); }, 1800);
  }

  var url = board.dataset.fragment, every = (parseInt(board.dataset.refresh, 10) || 60) * 1000, busy = false;
  function refresh() {
    if (busy || document.hidden || !url) return;
    busy = true;
    fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (r.status === 401 || r.redirected) { location.reload(); return null; }
      return r.ok ? r.text() : null;
    }).then(function (html) { if (html) board.innerHTML = html; }).catch(function () {}).then(function () { busy = false; });
  }
  setInterval(refresh, every);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
})();
