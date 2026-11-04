// หน้าต่างเด้ง «สรุปงานค้าง»: เปิดเองครั้งเดียวต่อการเข้าสู่ระบบ (หน้าหลัก) หรือกดปุ่ม «งานค้าง» ที่แถบบนได้ทุกหน้า
(function () {
  var dlg = document.getElementById('pending-dialog'), btn = document.getElementById('pending-open');
  if (!dlg || typeof dlg.showModal !== 'function') return;
  var body = dlg.querySelector('.pd-body'), opener = null;
  function load() {
    body.setAttribute('aria-busy', 'true'); body.textContent = 'กำลังโหลด…';
    fetch('/pending', { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) { body.innerHTML = html; })
      .catch(function () { body.innerHTML = '<p>โหลดสรุปงานค้างไม่สำเร็จ — <a href="/unit">เปิดหน้าหน่วยงานของฉัน</a> หรือลองใหม่อีกครั้ง</p>'; })
      .then(function () { body.removeAttribute('aria-busy'); });
  }
  function open(from) { opener = from || document.activeElement; if (!dlg.open) dlg.showModal(); load(); }
  function close() { if (dlg.open) dlg.close(); }
  dlg.addEventListener('close', function () { if (opener && opener.focus) opener.focus(); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg) close(); });                    // กดนอกกล่อง = ปิด
  dlg.querySelectorAll('[data-pd-close]').forEach(function (b) { b.addEventListener('click', close); });
  if (btn) btn.addEventListener('click', function () { open(btn); });
  if (dlg.getAttribute('data-auto') === '1') open(btn);
})();
