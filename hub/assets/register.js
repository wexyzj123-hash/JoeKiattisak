/* hub/assets/register.js — เลือกโรงงาน → แผนก → ไลน์ แบบต่อเนื่องในฟอร์มขอสร้างบัญชี (ข้อมูลจาก Safety) */
(function () {
  var data = document.getElementById('org-data');
  if (!data) return;
  var org = JSON.parse(data.textContent);
  var f = document.getElementById('f-factory'), d = document.getElementById('f-dept'), l = document.getElementById('f-line');
  function fill(sel, rows, keep, empty) {
    sel.innerHTML = '';
    var o = document.createElement('option'); o.value = ''; o.textContent = empty; sel.appendChild(o);
    rows.forEach(function (r) { var op = document.createElement('option'); op.value = r.id; op.textContent = r.name; if (String(r.id) === String(keep)) op.selected = true; sel.appendChild(op); });
  }
  function depts(keep) { fill(d, org.departments.filter(function (x) { return String(x.factory_id) === f.value; }), keep, f.value ? '— เลือกแผนก —' : '— เลือกโรงงานก่อน —'); lines(l.getAttribute('data-keep')); }
  function lines(keep) { fill(l, org.lines.filter(function (x) { return String(x.department_id) === d.value; }), keep, '— ไม่ระบุ —'); }
  f.addEventListener('change', function () { depts(''); });
  d.addEventListener('change', function () { lines(''); });
  depts(d.getAttribute('data-keep'));
})();
