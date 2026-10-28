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
  // ตารางที่ .table-stack: ใส่ชื่อคอลัมน์ให้ทุกเซลล์ตามตำแหน่งคอลัมน์จริง (นับ colspan) เพื่อแสดงบนจอแคบที่แต่ละแถวกลายเป็นการ์ด
  // (ไม่มี JS: ตารางยังเลื่อนแนวนอนได้ตามปกติ แต่ไม่มีป้ายชื่อคอลัมน์)
  function label() {
    var tables = document.querySelectorAll('table.table-stack');
    for (var t = 0; t < tables.length; t++) {
      var rows = tables[t].querySelectorAll('tr'), heads = null;
      for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].children, hasTd = false, i, col = 0;
        for (i = 0; i < cells.length; i++) if (cells[i].tagName === 'TD') hasTd = true;
        if (!hasTd) {
          rows[r].className += ' hrow';
          if (!heads) {
            heads = [];
            for (i = 0; i < cells.length; i++) {
              var txt = (cells[i].textContent || '').replace(/[▲▼↑↓]/g, '').replace(/\s+/g, ' ').trim();
              for (var k = 0; k < (cells[i].colSpan || 1); k++) heads.push(txt);
            }
          }
          continue;
        }
        for (i = 0; i < cells.length; i++) {
          var span = cells[i].colSpan || 1;
          if (heads && span === 1 && heads[col] && !cells[i].hasAttribute('data-label')) cells[i].setAttribute('data-label', heads[col]);
          col += span;
        }
      }
    }
  }
  // เมนูผู้ใช้ (<details>): ปิดด้วย Esc และเมื่อโฟกัส/คลิกออกนอกเมนู
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = document.querySelector('details.user-menu[open]');
    if (open) { open.removeAttribute('open'); var s = open.querySelector('summary'); if (s) s.focus(); }
  });
  document.addEventListener('click', function (e) {
    var open = document.querySelector('details.user-menu[open]');
    if (open && !open.contains(e.target)) open.removeAttribute('open');
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', label); else label();
})();
