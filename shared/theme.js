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
  // ตารางที่ .table-stack: ใส่ชื่อคอลัมน์ให้ทุกเซลล์ (ใช้แสดงบนจอแคบที่แต่ละแถวกลายเป็นการ์ด); ไม่มี JS ก็ยังเป็นตารางเลื่อนได้ปกติ
  function label() {
    var tables = document.querySelectorAll('table.table-stack');
    for (var t = 0; t < tables.length; t++) {
      var rows = tables[t].querySelectorAll('tr'), heads = null;
      for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].children, hasTd = false, i;
        for (i = 0; i < cells.length; i++) if (cells[i].tagName === 'TD') hasTd = true;
        if (!hasTd) { rows[r].className += ' hrow'; if (!heads) { heads = []; for (i = 0; i < cells.length; i++) heads.push((cells[i].textContent || '').replace(/\s+/g, ' ').trim()); } continue; }
        if (!heads) continue;
        for (i = 0; i < cells.length; i++) if (!cells[i].hasAttribute('data-label') && !cells[i].hasAttribute('colspan') && heads[i]) cells[i].setAttribute('data-label', heads[i]);
      }
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', label); else label();
})();
