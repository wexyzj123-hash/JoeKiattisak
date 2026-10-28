from datetime import date

import pytest

from safety import patrol
from safety.db import connect
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_user


@pytest.fixture
def db(tmp_path):
    con = connect(str(tmp_path / "s.db"))
    ensure_schema(con)
    return con


class W:
    def __init__(self, db):
        self.db = db
        self.ch = add_chain(db, "P")
        self.insp = add_user(db, "assessor", "ผู้ตรวจ")
        self.cl = db.execute("INSERT INTO checklists(code,name_th) VALUES('CL1','แบบตรวจ 1')").lastrowid
        db.execute("INSERT INTO checklist_items(checklist_id, section, text_th) VALUES(?,?,?)", (self.cl, "หมวด", "ข้อ 1"))
        db.commit()

    def plan(self, freq="weekly", created="2026-01-01 08:00:00", **kw):
        cols = {"checklist_id": self.cl, "risk_area_id": self.ch["a"], "frequency": freq, "created_at": created, **kw}
        cur = self.db.execute(f"INSERT INTO patrol_plans({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
        self.db.commit()
        return self.db.execute("SELECT * FROM patrol_plans WHERE id=?", (cur.lastrowid,)).fetchone()

    def submitted(self, when: str, n=[0]):
        n[0] += 1
        self.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status, submitted_at) VALUES(?,?,?,?, 'submitted', ?)",
                        (f"PT-X-{n[0]:04d}-{when}", self.cl, self.ch["a"], self.insp, when))
        self.db.commit()


@pytest.fixture
def w(db):
    return W(db)


D = date


@pytest.mark.parametrize("freq, last, today, due, state, days", [
    ("daily", None, D(2026, 1, 1), D(2026, 1, 1), "due", 0),                              # ไม่เคยตรวจ: ครบกำหนดตั้งแต่วันสร้างแผน
    ("daily", None, D(2026, 1, 5), D(2026, 1, 1), "overdue", 4),
    ("daily", "2026-03-10 09:00:00", D(2026, 3, 10), D(2026, 3, 11), "ok", 1),
    ("daily", "2026-03-10 09:00:00", D(2026, 3, 11), D(2026, 3, 11), "due", 0),
    ("daily", "2026-03-06 09:00:00", D(2026, 3, 9), D(2026, 3, 7), "overdue", 2),         # วันศุกร์ → ทวงวันจันทร์: เลยมา 2 วัน (ไม่ข้ามเสาร์-อาทิตย์)
    ("weekly", "2026-03-03 09:00:00", D(2026, 3, 10), D(2026, 3, 10), "due", 0),
    ("weekly", "2026-03-03 09:00:00", D(2026, 3, 9), D(2026, 3, 10), "ok", 1),
    ("weekly", "2026-03-03 09:00:00", D(2026, 3, 11), D(2026, 3, 10), "overdue", 1),
    ("weekly", "2026-01-05 09:00:00", D(2026, 3, 30), D(2026, 1, 12), "overdue", 77),    # พลาดหลายรอบ = สถานะเดียว
    ("monthly", "2026-01-31 10:00:00", D(2026, 2, 28), D(2026, 2, 28), "due", 0),         # 31 ม.ค. → 28 ก.พ.
    ("monthly", "2028-01-31 10:00:00", D(2028, 2, 28), D(2028, 2, 29), "ok", 1),          # ปีอธิกสุรทิน → 29 ก.พ.
    ("monthly", "2025-12-15 10:00:00", D(2026, 1, 14), D(2026, 1, 15), "ok", 1),          # ข้ามปี
    ("monthly", "2026-03-30 10:00:00", D(2026, 4, 30), D(2026, 4, 30), "due", 0),
])
def test_plan_status_table(w, freq, last, today, due, state, days):
    plan = w.plan(freq)
    if last:
        w.submitted(last)
    st = patrol.plan_status(w.db, plan, today)
    assert (st["due"], st["state"], st["days"]) == (due, state, days)
    assert st["last"] == (date.fromisoformat(last[:10]) if last else None)


def test_status_uses_latest_submitted_round_for_that_checklist_and_area_only_and_ignores_drafts(w):
    plan = w.plan("weekly")
    w.submitted("2026-03-01 09:00:00"); w.submitted("2026-03-08 09:00:00")
    other = add_chain(w.db, "Q")
    w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status, submitted_at) VALUES('PT-OTHER',?,?,?, 'submitted','2026-03-09 09:00:00')",
                 (w.cl, other["a"], w.insp))
    w.db.execute("INSERT INTO patrol_rounds(code, checklist_id, risk_area_id, inspector_id, status) VALUES('PT-DRAFT',?,?,?, 'draft')", (w.cl, w.ch["a"], w.insp))
    w.db.commit()
    assert patrol.plan_status(w.db, plan, D(2026, 3, 9))["due"] == D(2026, 3, 15)
