"""ปัญหาเล็กน้อยที่เลื่อนไว้ของเฟส 8: งวดค้างเก่าสุดไม่หาย · งวดต้องจบครบก่อนบันทึก · ขอบเขตวัน/ปี · PDF escape ข้อความ"""
from datetime import date

from safety import report_due as rd, report_export as ex, reports
from tests.safety.test_report_due import W, db, fixed  # noqa: F401
from tests.safety.test_report_export import rep


def test_older_unsubmitted_period_stays_overdue_after_the_next_due_date_passes(db, fixed):
    w = W(db)
    r = w.rule(day=5, created="2026-01-01 00:00")
    st = rd.status(db, r, date(2026, 3, 20))                       # งวด 2025-12 (กำหนด 5 ม.ค.) ยังไม่ส่ง แม้กำหนดของ 2026-01/02 ผ่านไปแล้ว
    assert (st["state"], st["period"], st["due"]) == ("overdue", "2025-12", date(2026, 1, 5))
    assert rd.mark_submitted(db, w.admin, "monthly", None, "2025-12", "") == []
    assert rd.status(db, r, date(2026, 3, 20))["period"] == "2026-01"
    for p in ("2026-01", "2026-02"):
        assert rd.mark_submitted(db, w.admin, "monthly", None, p, "") == []
    assert rd.status(db, r, date(2026, 3, 20))["state"] == "upcoming"


def test_history_window_is_bounded_and_respects_rule_creation_date(db, fixed):
    w = W(db)
    old = w.rule(day=5, created="2020-01-01 00:00")
    assert rd.status(db, old, date(2026, 3, 20))["period"] == "2025-02"          # ย้อนไม่เกิน 12 งวด
    fresh = w.rule(key="capa", day=5, created="2026-03-10 00:00")
    assert rd.status(db, fresh, date(2026, 3, 20))["state"] == "upcoming"         # กำหนดก่อนวันสร้างไม่นับ


def test_period_validation_is_enforced_for_every_submit_path(db, fixed):
    w = W(db)
    for report, period in (("monthly", "2026"), ("annual", "2025-12"), ("monthly", "2026-03"), ("annual", "2026"), ("monthly", "2026-13"), ("monthly", "x")):
        assert rd.mark_submitted(db, w.admin, report, None, period, ""), (report, period)
    assert rd.mark_submitted(db, w.admin, "annual", None, "2025", "") == []


def test_month_bounds_across_year_and_leap_february():
    assert reports._month_bounds(2026, 12) == ("2026-12", "2026-12-01", "2026-12-31")
    assert reports._month_bounds(2027, 1) == ("2027-01", "2027-01-01", "2027-01-31")
    assert reports._month_bounds(2028, 2)[2] == "2028-02-29" and reports._month_bounds(2027, 2)[2] == "2027-02-28"
    w_due = {"frequency": "monthly", "due_day": 31, "due_month": None}
    assert rd._due_date(w_due, 2028, 2) == date(2028, 2, 28)                      # วันครบกำหนดถูกจำกัดที่วันที่ 28
    assert rd.period_due(w_due, date(2026, 12, 20))[0] == "2026-11"
    assert rd.period_due(w_due, date(2026, 12, 31)) == ("2026-12", date(2027, 1, 28))


def test_pdf_cells_are_escaped_before_reaching_reportlab(monkeypatch):
    seen = []
    real = ex.Paragraph
    monkeypatch.setattr(ex, "Paragraph", lambda text, style, *a, **k: (seen.append(text), real(text, style, *a, **k))[1])
    assert ex.to_pdf(rep(tables=[reports.Table("<b>t</b>", ["<i>a</i>"], [["<b>&</b> </para>"]])], title="<x>")).startswith(b"%PDF")
    joined = "\n".join(seen)
    assert "&lt;b&gt;&amp;&lt;/b&gt; &lt;/para&gt;" in joined and "&lt;i&gt;a&lt;/i&gt;" in joined and "&lt;x&gt;" in joined
    assert "<b>&" not in joined and "<i>" not in joined and "</para>" not in joined
