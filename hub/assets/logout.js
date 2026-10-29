/* hub/assets/logout.js — สั่ง Safety ล้าง session ของตัวเองเบื้องหลัง แล้วกลับหน้า Hub (หน้าแรก เข้าดูได้โดยไม่ต้องล็อกอิน) เสมอ (Safety ไม่ตอบก็ไม่ค้าง) */
(function () {
  var card = document.getElementById('logout-card');
  var target = card ? card.getAttribute('data-signout') : '';
  var done = false;
  function go() { if (!done) { done = true; location.replace('/'); } }
  setTimeout(go, 2500);
  if (!target || !window.fetch) { go(); return; }
  fetch(target, { mode: 'no-cors', credentials: 'include', cache: 'no-store' }).then(go, go);
})();
