"""safety/reports.py — รายงานและสถิติ: สร้างโครงสร้างกลาง (Report) จากข้อมูลจริงตามขอบเขตโรงงาน/แผนกของผู้ใช้
หน้าจอ Excel และ PDF ใช้โครงสร้างเดียวกัน จึงตัวเลขตรงกันเสมอ; ไม่มีชื่อผู้บาดเจ็บ/ข้อมูลส่วนบุคคลในรายงานใด ๆ"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import timeutil
from .hazards import STATUS_TH, hazard_scope_sql
from .stats import _high_sql

REPORTS = {"monthly": "รายงานประจำเดือนตามแผนก", "annual": "สรุปประจำปี", "incidents": "สถิติอุบัติเหตุ", "capa": "สถานะ CAPA"}
MONTHS_TH = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")
KIND_TH = {"accident": "อุบัติเหตุ", "near_miss": "Near Miss"}
CAPA_STATUS_TH = {"open": "รอดำเนินการ", "in_progress": "กำลังดำเนินการ", "done": "รอยืนยันผล", "verified": "ยืนยันผลแล้ว", "cancelled": "ยกเลิก"}
OPEN_H = ("reported", "assessed", "assigned")


@dataclass
class Kpi:
    label: str
    value: object
    note: str = ""


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list]
    empty_text: str = "ไม่มีข้อมูล"


@dataclass
class Report:
    key: str
    title: str
    subtitle: str
    period_label: str
    generated_at: str
    kpis: list[Kpi] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


def _check_period(year: int, month: int | None = None) -> None:
    if not 2000 <= year <= 2100:
        raise ValueError("ปีไม่ถูกต้อง")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("เดือนไม่ถูกต้อง")


def _cond(user, alias: str, dept_id: int | None = None) -> tuple[str, list]:
    sql, args = hazard_scope_sql(user)
    sql = sql.replace("h.risk_area_id", f"{alias}.risk_area_id")
    args = list(args)
    if dept_id is not None:
        sql += (f" AND {alias}.risk_area_id IN (SELECT ra2.id FROM risk_areas ra2 JOIN lines l2 ON l2.id = ra2.line_id "
                "WHERE l2.department_id = ?)")
        args.append(dept_id)
    return sql, args


def department_in_scope(db, user, dept_id: int):
    conds, args = ["d.id = ?"], [dept_id]
    if user["factory_id"] is not None:
        conds.append("d.factory_id = ?"); args.append(user["factory_id"])
    if user["department_id"] is not None:
        conds.append("d.id = ?"); args.append(user["department_id"])
    return db.execute(f"SELECT d.id, d.name_th, f.name_th AS factory FROM departments d JOIN factories f ON f.id = d.factory_id WHERE {' AND '.join(conds)}",
                      args).fetchone()


def _now_text() -> str:
    return timeutil.now().strftime("%d/%m/%Y %H:%M")


def _month_bounds(year: int, month: int) -> tuple[str, str, str]:
    """(ym, ต้นเดือน YYYY-MM-01, วันสุดท้ายของเดือน)"""
    first = date(year, month, 1)
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return f"{year:04d}-{month:02d}", first.isoformat(), last.isoformat()


def _n(db, sql, args) -> int:
    return db.execute(sql, args).fetchone()[0] or 0


def _closed_in(db, cond, args, like: str) -> int:
    return _n(db, f"SELECT COUNT(DISTINCT e.hazard_id) FROM hazard_events e JOIN hazards h ON h.id = e.hazard_id "
                  f"WHERE e.to_status = 'closed' AND e.at LIKE ? AND {cond}", [like + "%"] + args)


def _open_at(db, cond, args, end: str, extra: str = "", extra_args: list | None = None) -> int:
    return _n(db, f"SELECT COUNT(*) FROM hazards h WHERE {cond} AND substr(h.reported_at,1,10) <= ? {extra} "
                  "AND NOT EXISTS (SELECT 1 FROM hazard_events e WHERE e.hazard_id = h.id AND e.to_status IN ('closed','rejected') "
                  "AND substr(e.at,1,10) <= ?)", args + [end] + (extra_args or []) + [end])


def _lost_time(db, cond, args, like: str | None = None, upto: str | None = None) -> int:
    where, a = "", list(args)
    if like:
        where += " AND i.occurred_at LIKE ?"; a.append(like + "%")
    if upto:
        where += " AND substr(i.occurred_at,1,10) <= ?"; a.append(upto)
    return _n(db, f"SELECT COUNT(*) FROM incidents i JOIN injury_severities s ON s.id = i.injury_severity_id "
                  f"WHERE {cond} AND i.kind='accident' AND i.status != 'rejected' AND s.counts_lost_time = 1{where}", a)


def _days_since_lost_time(db, cond, args, as_of: date):
    row = db.execute(f"SELECT MAX(substr(i.occurred_at,1,10)) FROM incidents i JOIN injury_severities s ON s.id = i.injury_severity_id "
                     f"WHERE {cond} AND i.kind='accident' AND i.status != 'rejected' AND s.counts_lost_time = 1 AND substr(i.occurred_at,1,10) <= ?",
                     args + [as_of.isoformat()]).fetchone()
    if row is None or row[0] is None:
        return "ไม่มีข้อมูล"
    return (as_of - date.fromisoformat(row[0])).days


def monthly_department(db, user, year: int, month: int, department_id: int | None = None) -> Report | None:
    _check_period(year, month)
    scope = ""
    if department_id is not None:
        dep = department_in_scope(db, user, department_id)
        if dep is None:
            return None
        scope = f"{dep['factory']} / {dep['name_th']}"
    ym, first, last = _month_bounds(year, month)
    hc, ha = _cond(user, "h", department_id)
    ic, ia = _cond(user, "i", department_id)
    cc, ca = _cond(user, "c", department_id)
    rc, ra = _cond(user, "r", department_id)
    hi_sql, hi_args = _high_sql(db)
    as_of = min(timeutil.today(), date.fromisoformat(last))
    reported = _n(db, f"SELECT COUNT(*) FROM hazards h WHERE {hc} AND h.status != 'rejected' AND h.reported_at LIKE ?", ha + [ym + "%"])
    kpis = [
        Kpi("แจ้งอันตราย", reported),
        Kpi("ปิดแล้ว", _closed_in(db, hc, ha, ym)),
        Kpi("ค้างปลายเดือน", _open_at(db, hc, ha, last)),
        Kpi("เกินกำหนดปลายเดือน", _open_at(db, hc, ha, last, "AND h.due_date IS NOT NULL AND h.due_date < ?", [last])),
        Kpi("ความเสี่ยงสูงที่ยังค้าง", _open_at(db, hc, ha, last, f"AND {hi_sql}", hi_args)),
        Kpi("อุบัติเหตุ", _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {ic} AND i.kind='accident' AND i.status != 'rejected' AND i.occurred_at LIKE ?", ia + [ym + "%"])),
        Kpi("Near Miss", _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {ic} AND i.kind='near_miss' AND i.status != 'rejected' AND i.occurred_at LIKE ?", ia + [ym + "%"])),
        Kpi("อุบัติเหตุหยุดงาน", _lost_time(db, ic, ia, ym)),
        Kpi("วันปลอดอุบัติเหตุหยุดงาน", _days_since_lost_time(db, ic, ia, as_of), "นับถึงสิ้นเดือนนี้หรือวันนี้"),
    ]
    pr = db.execute(f"SELECT COUNT(*), AVG(r.score_pct) FROM patrol_rounds r WHERE {rc} AND r.status='submitted' AND r.submitted_at LIKE ?", ra + [ym + "%"]).fetchone()
    kpis += [Kpi("รอบตรวจที่ส่งแล้ว", pr[0]), Kpi("คะแนนตรวจเฉลี่ย", f"{pr[1]:.0f}%" if pr[1] is not None else "-"),
             Kpi("CAPA ที่เปิด", _n(db, f"SELECT COUNT(*) FROM capas c WHERE {cc} AND c.status != 'cancelled' AND c.created_at LIKE ?", ca + [ym + "%"])),
             Kpi("CAPA ที่ยืนยันผลแล้ว", _n(db, f"SELECT COUNT(*) FROM capas c WHERE {cc} AND c.verified_at LIKE ?", ca + [ym + "%"]))]
    by_type = db.execute(f"SELECT t.name_th, COUNT(*) FROM hazards h JOIN hazard_types t ON t.id = h.hazard_type_id WHERE {hc} AND h.reported_at LIKE ? "
                         "GROUP BY t.id ORDER BY 2 DESC, t.name_th", ha + [ym + "%"]).fetchall()
    by_area = db.execute(f"SELECT a.name_th, COUNT(*) FROM hazards h JOIN risk_areas a ON a.id = h.risk_area_id WHERE {hc} AND h.reported_at LIKE ? "
                         "GROUP BY a.id ORDER BY 2 DESC, a.name_th", ha + [ym + "%"]).fetchall()
    inc = db.execute(f"SELECT i.code, substr(i.occurred_at,1,10), i.kind, COALESCE(s.name_th,'-'), a.name_th FROM incidents i JOIN risk_areas a ON a.id = i.risk_area_id "
                     f"LEFT JOIN injury_severities s ON s.id = i.injury_severity_id WHERE {ic} AND i.status != 'rejected' AND i.occurred_at LIKE ? "
                     "ORDER BY i.occurred_at, i.code", ia + [ym + "%"]).fetchall()
    overdue = db.execute(f"SELECT h.code, h.title, a.name_th, h.due_date FROM hazards h JOIN risk_areas a ON a.id = h.risk_area_id WHERE {hc} "
                         "AND substr(h.reported_at,1,10) <= ? AND h.due_date IS NOT NULL AND h.due_date < ? AND NOT EXISTS (SELECT 1 FROM hazard_events e "
                         "WHERE e.hazard_id = h.id AND e.to_status IN ('closed','rejected') AND substr(e.at,1,10) <= ?) ORDER BY h.due_date, h.code",
                         ha + [last, last, last]).fetchall()
    tables = [
        Table("อันตรายที่แจ้งแยกตามประเภท", ["ประเภทอันตราย", "จำนวน"], [list(r) for r in by_type]),
        Table("อันตรายที่แจ้งแยกตามพื้นที่", ["พื้นที่", "จำนวน"], [list(r) for r in by_area]),
        Table("อุบัติเหตุ/Near Miss ในเดือน", ["รหัส", "วันที่เกิดเหตุ", "ประเภท", "ระดับการบาดเจ็บ", "พื้นที่"],
              [[r[0], r[1], KIND_TH.get(r[2], r[2]), r[3], r[4]] for r in inc]),
        Table("รายการอันตรายที่เกินกำหนด ณ สิ้นเดือน", ["รหัส", "หัวข้อ", "พื้นที่", "กำหนดแล้วเสร็จ"], [list(r) for r in overdue]),
    ]
    return Report("monthly", REPORTS["monthly"], scope or "ทุกแผนกในขอบเขตของคุณ", f"{MONTHS_TH[month - 1]} {year + 543}", _now_text(), kpis, tables)


def annual_summary(db, user, year: int) -> Report:
    _check_period(year)
    hc, ha = _cond(user, "h")
    ic, ia = _cond(user, "i")
    rows, tot = [], [0] * 5
    for m in range(1, 13):
        ym = f"{year:04d}-{m:02d}"
        vals = [_n(db, f"SELECT COUNT(*) FROM hazards h WHERE {hc} AND h.status != 'rejected' AND h.reported_at LIKE ?", ha + [ym + "%"]),
                _closed_in(db, hc, ha, ym),
                _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {ic} AND i.kind='accident' AND i.status != 'rejected' AND i.occurred_at LIKE ?", ia + [ym + "%"]),
                _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {ic} AND i.kind='near_miss' AND i.status != 'rejected' AND i.occurred_at LIKE ?", ia + [ym + "%"]),
                _lost_time(db, ic, ia, ym)]
        tot = [a + b for a, b in zip(tot, vals)]
        rows.append([MONTHS_TH[m - 1], *vals])
    rows.append(["รวมทั้งปี", *tot])
    dep_rows = []
    for d in db.execute("SELECT d.id, d.name_th, f.name_th AS factory FROM departments d JOIN factories f ON f.id = d.factory_id ORDER BY f.name_th, d.name_th"):
        if department_in_scope(db, user, d["id"]) is None:
            continue
        dc, da = _cond(user, "h", d["id"]); dic, dia = _cond(user, "i", d["id"])
        dep_rows.append([f"{d['factory']} / {d['name_th']}",
                         _n(db, f"SELECT COUNT(*) FROM hazards h WHERE {dc} AND h.reported_at LIKE ?", da + [f"{year:04d}%"]),
                         _n(db, f"SELECT COUNT(*) FROM incidents i WHERE {dic} AND i.status != 'rejected' AND i.occurred_at LIKE ?", dia + [f"{year:04d}%"])])
    kpis = [Kpi("แจ้งอันตรายทั้งปี", tot[0]), Kpi("ปิดแล้วทั้งปี", tot[1]), Kpi("อุบัติเหตุ", tot[2]), Kpi("Near Miss", tot[3]), Kpi("อุบัติเหตุหยุดงาน", tot[4])]
    tables = [Table("สรุปรายเดือน", ["เดือน", "อันตรายที่แจ้ง", "ปิดแล้ว", "อุบัติเหตุ", "Near Miss", "หยุดงาน"], rows),
              Table("แยกตามแผนก", ["แผนก", "อันตรายที่แจ้ง", "อุบัติเหตุ/Near Miss"], [r for r in dep_rows if r[1] or r[2]])]
    return Report("annual", REPORTS["annual"], "ทุกแผนกในขอบเขตของคุณ", f"ปี {year + 543}", _now_text(), kpis, tables)


def incident_statistics(db, user, year: int) -> Report:
    _check_period(year)
    ic, ia = _cond(user, "i")
    live, y = "i.status != 'rejected' AND i.occurred_at LIKE ?", [f"{year:04d}%"]
    kind = {r[0]: r[1] for r in db.execute(f"SELECT i.kind, COUNT(*) FROM incidents i WHERE {ic} AND {live} GROUP BY i.kind", ia + y)}
    lost_days = _n(db, f"SELECT SUM(i.lost_days) FROM incidents i WHERE {ic} AND {live} AND i.kind='accident'", ia + y)
    months = []
    for m in range(1, 13):
        ym = f"{year:04d}-{m:02d}%"
        months.append([MONTHS_TH[m - 1]] + [_n(db, f"SELECT COUNT(*) FROM incidents i WHERE {ic} AND i.kind=? AND i.status != 'rejected' AND i.occurred_at LIKE ?", ia + [k, ym])
                                            for k in ("accident", "near_miss")])
    sev = db.execute(f"SELECT s.name_th, COUNT(*) FROM incidents i JOIN injury_severities s ON s.id = i.injury_severity_id WHERE {ic} AND {live} AND i.kind='accident' "
                     "GROUP BY s.id ORDER BY s.rank, s.id", ia + y).fetchall()
    area = db.execute(f"SELECT a.name_th, COUNT(*) FROM incidents i JOIN risk_areas a ON a.id = i.risk_area_id WHERE {ic} AND {live} GROUP BY a.id ORDER BY 2 DESC, a.name_th LIMIT 10", ia + y).fetchall()
    cause = db.execute(f"SELECT COALESCE(c.name_th,'ยังไม่ระบุสาเหตุ'), COUNT(*) FROM incidents i LEFT JOIN incident_causes c ON c.id = i.cause_id WHERE {ic} AND {live} "
                       "GROUP BY c.id ORDER BY 2 DESC, 1", ia + y).fetchall()
    status = {r[0]: r[1] for r in db.execute(f"SELECT i.status, COUNT(*) FROM incidents i WHERE {ic} AND {live} GROUP BY i.status", ia + y)}
    kpis = [Kpi("อุบัติเหตุ", kind.get("accident", 0)), Kpi("Near Miss", kind.get("near_miss", 0)), Kpi("อุบัติเหตุหยุดงาน", _lost_time(db, ic, ia, f"{year:04d}")),
            Kpi("วันหยุดงานรวม", lost_days), Kpi("ปิดเรื่องแล้ว", status.get("closed", 0))]
    tables = [Table("รายเดือน", ["เดือน", "อุบัติเหตุ", "Near Miss"], months),
              Table("อุบัติเหตุแยกตามระดับการบาดเจ็บ", ["ระดับ", "จำนวน"], [list(r) for r in sev]),
              Table("พื้นที่ที่เกิดเหตุสูงสุด", ["พื้นที่", "จำนวน"], [list(r) for r in area]),
              Table("แยกตามสาเหตุ", ["สาเหตุ", "จำนวน"], [list(r) for r in cause])]
    return Report("incidents", REPORTS["incidents"], "ทุกแผนกในขอบเขตของคุณ", f"ปี {year + 543}", _now_text(), kpis, tables)


def capa_status(db, user, as_of: date | None = None) -> Report:
    as_of = as_of or timeutil.today()
    cc, ca = _cond(user, "c")
    counts = {r[0]: r[1] for r in db.execute(f"SELECT c.status, COUNT(*) FROM capas c WHERE {cc} GROUP BY c.status", ca)}
    iso = as_of.isoformat()
    over = db.execute(f"SELECT c.code, c.title, u.name, c.due_date FROM capas c JOIN safety_users u ON u.id = c.owner_id WHERE {cc} "
                      "AND c.status IN ('open','in_progress') AND c.due_date < ? ORDER BY c.due_date, c.code", ca + [iso]).fetchall()
    avg = db.execute(f"SELECT AVG(julianday(substr(c.verified_at,1,10)) - julianday(substr(c.created_at,1,10))) FROM capas c WHERE {cc} AND c.status='verified' AND c.verified_at IS NOT NULL", ca).fetchone()[0]
    eff = _n(db, f"SELECT COUNT(*) FROM capas c WHERE {cc} AND c.status='verified' AND c.effectiveness_due IS NOT NULL AND c.effectiveness_result IS NULL AND c.effectiveness_due <= ?", ca + [iso])
    kpis = [Kpi(CAPA_STATUS_TH[s], counts.get(s, 0)) for s in ("open", "in_progress", "done", "verified")]
    kpis += [Kpi("เกินกำหนด", len(over)), Kpi("ถึงกำหนดติดตามผลแล้วยังไม่บันทึก", eff), Kpi("เวลาเฉลี่ยจนยืนยันผล (วัน)", f"{avg:.1f}" if avg is not None else "-")]
    by_src = db.execute(f"SELECT c.source_kind, COUNT(*) FROM capas c WHERE {cc} AND c.status != 'cancelled' GROUP BY c.source_kind ORDER BY 2 DESC", ca).fetchall()
    src_th = {"hazard": "อันตราย", "incident": "อุบัติเหตุ", "patrol": "Safety Patrol", "manual": "สร้างเอง"}
    tables = [Table("CAPA ที่เกินกำหนด", ["รหัส", "หัวข้อ", "เจ้าของงาน", "กำหนดแล้วเสร็จ", "เกินมา (วัน)"],
                    [[r[0], r[1], r[2], r[3], (as_of - date.fromisoformat(r[3])).days] for r in over]),
              Table("แยกตามแหล่งที่มา", ["แหล่งที่มา", "จำนวน"], [[src_th.get(r[0], r[0]), r[1]] for r in by_src])]
    return Report("capa", REPORTS["capa"], "ทุกแผนกในขอบเขตของคุณ", f"ณ วันที่ {as_of.day} {MONTHS_TH[as_of.month - 1]} {as_of.year + 543}", _now_text(), kpis, tables)


def build(db, user, key: str, year: int, month: int, department_id: int | None = None) -> Report | None:
    if key == "monthly":
        return monthly_department(db, user, year, month, department_id)
    if key == "annual":
        return annual_summary(db, user, year)
    if key == "incidents":
        return incident_statistics(db, user, year)
    if key == "capa":
        return capa_status(db, user)
    return None
