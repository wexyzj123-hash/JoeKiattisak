/* shared/theme.js — สลับธีมสว่าง/มืดและจำค่าไว้ (localStorage อาจใช้ไม่ได้ เช่น โหมดส่วนตัว — ห่อด้วย try/catch ทุกจุด) */
(function () {
  var KEY = 'aiat-theme', root = document.documentElement;
  function read() { try { return window.localStorage.getItem(KEY); } catch (e) { return null; } }
  function write(v) { try { window.localStorage.setItem(KEY, v); } catch (e) { /* ใช้งานต่อได้แม้จำค่าไม่ได้ */ } }
  function apply(v) { if (v === 'dark' || v === 'light') root.setAttribute('data-theme', v); else root.removeAttribute('data-theme'); }
  function current() {
    var set = root.getAttribute('data-theme');
    if (set) return set;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  apply(read());
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-theme-toggle]') : null;
    if (!el) return;
    var next = current() === 'dark' ? 'light' : 'dark';
    apply(next); write(next);
  });
})();
