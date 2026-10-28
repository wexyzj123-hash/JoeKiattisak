"""safety/scheduler.py — งานประจำวัน: เตือนใกล้ครบกำหนด/เกินกำหนด (ยกระดับ L1–L3 ตามกฎ) + สำรองฐานข้อมูล
- รันได้วันละครั้ง (job_runs เป็นตัวจอง — หลายตัวทำงานพร้อมกันก็มีผู้ชนะเพียงตัวเดียว)
- ทำงานตามเวลาใน app_settings.daily_time และตามทันเมื่อเปิดเครื่องสาย (due_run_needed)
- แต่ละรายการอันตรายแยกกัน: รายการเสียหนึ่งรายการไม่ทำให้ทั้งรอบล้ม
- วันที่พลาดไปไม่ถูกส่งย้อนหลัง: คำนวณจากวันนี้ ขั้นที่ตรงกับ offset ของวันนี้เท่านั้น (ไม่ท่วมผู้ใช้)"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta

from . import notify, patrol, report_due, timeutil
from .db import connect

log = logging.getLogger("safety.scheduler")
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
BACKUP_RE = re.compile(r"^safety-\d{8}\.db$")
OPEN = ("reported", "assessed", "assigned")


def backup_database(db_path: str, backup_dir: str, today: date, keep: int = 14) -> str:
    """สำรองด้วย sqlite backup API เขียนไฟล์ชั่วคราวแล้วเปลี่ยนชื่อ (ไม่เหลือไฟล์ไม่สมบูรณ์) และเก็บล่าสุด keep ไฟล์"""
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, f"safety-{today:%Y%m%d}.db")
    tmp = dest + ".tmp"
    src = dst = None
    try:
        src, dst = sqlite3.connect(db_path), sqlite3.connect(tmp)
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")          # ไฟล์สำรองเป็นไฟล์เดี่ยว ไม่มี -wal/-shm ติดมา
        dst.close(); dst = None
        os.replace(tmp, dest)
    except BaseException:
        if dst is not None:
            dst.close()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        if src is not None:
            src.close()
    for old in sorted((f for f in os.listdir(backup_dir) if BACKUP_RE.match(f)), reverse=True)[keep:]:
        os.remove(os.path.join(backup_dir, old))
    return dest


def _daily_time(db) -> tuple[int, int]:
    row = db.execute("SELECT value FROM app_settings WHERE key='daily_time'").fetchone()
    m = HHMM_RE.match(row[0]) if row else None
    return (int(m.group(1)), int(m.group(2))) if m else (7, 30)


STALE_MINUTES = 30                 # รอบที่เริ่มแล้วแต่ไม่เสร็จเกินเวลานี้ถือว่าล้ม (โปรเซสตาย/ฐานข้อมูลล็อก) และเริ่มใหม่ได้
fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")


def due_run_needed(db, now: datetime) -> bool:
    """ถึงเวลาแล้ว (หรือเลยเวลาแล้ว = ตามทัน) และวันนี้ยังไม่เคยรัน — หรือเคยเริ่มแต่ไม่เสร็จนานเกิน STALE_MINUTES"""
    hour, minute = _daily_time(db)
    if (now.hour, now.minute) < (hour, minute):
        return False
    row = db.execute("SELECT finished_at, started_at FROM job_runs WHERE job='daily' AND run_date=?", (now.date().isoformat(),)).fetchone()
    if row is None:
        return True
    return row[0] is None and (row[1] is None or row[1] <= fmt(now - timedelta(minutes=STALE_MINUTES)))


def _offsets(db, events: tuple[str, ...]) -> dict[str, list[int]]:
    return {ev: sorted(r[0] for r in db.execute("SELECT DISTINCT offset_days FROM notify_rules WHERE event=? AND enabled=1", (ev,))) for ev in events}


def _guarded(db, label: str, ident, fn) -> tuple[int, int]:
    """ทำทีละรายการใน SAVEPOINT: รายการเดียวเสียไม่ทำให้ทั้งรอบล้ม; คืน (จำนวนแจ้งเตือน, ข้อผิดพลาด)"""
    try:
        db.execute("SAVEPOINT daily_item")
        n = fn()
        db.execute("RELEASE daily_item")
        db.commit()
        return n, 0
    except Exception:                                      # noqa: BLE001
        log.exception("daily reminder failed for %s %s", label, ident)
        try:
            db.execute("ROLLBACK TO daily_item"); db.execute("RELEASE daily_item")
        except sqlite3.Error:
            db.rollback()
        return 0, 1


def _capa_reminders(db, today: date) -> tuple[int, int]:
    offsets = _offsets(db, ("capa_due_soon", "capa_overdue"))
    total = errors = 0
    for capa_id, due_text in db.execute("SELECT id, due_date FROM capas WHERE status IN ('open','in_progress') ORDER BY id").fetchall():
        def work(capa_id=capa_id, due_text=due_text):
            days, n = (date.fromisoformat(due_text) - today).days, 0
            if days >= 0:
                for off in offsets["capa_due_soon"]:
                    if off >= days:
                        n += notify.emit_capa(db, "capa_due_soon", capa_id, None, offset_days=off, days=days)
            else:
                for off in offsets["capa_overdue"]:
                    if off <= -days:
                        n += notify.emit_capa(db, "capa_overdue", capa_id, None, offset_days=off, days=-days)
            return n
        n, e = _guarded(db, "capa", capa_id, work)
        total, errors = total + n, errors + e
    return total, errors


def _patrol_reminders(db, today: date) -> tuple[int, int]:
    offsets = _offsets(db, ("patrol_overdue",))
    total = errors = 0
    plans = db.execute("SELECT p.* FROM patrol_plans p JOIN checklists c ON c.id = p.checklist_id WHERE p.active=1 AND c.active=1 ORDER BY p.id").fetchall()
    for plan in plans:
        def work(plan=plan):
            st = patrol.plan_status(db, plan, today)
            if st["state"] == "ok":
                return 0
            due, n = st["due"].isoformat(), notify.emit_patrol(db, "patrol_due", plan["id"], None, offset_days=0, days=0, due=st["due"].isoformat())
            for off in offsets["patrol_overdue"]:
                if off <= st["days"]:
                    n += notify.emit_patrol(db, "patrol_overdue", plan["id"], None, offset_days=off, days=st["days"], due=due)
            return n
        n, e = _guarded(db, "patrol plan", plan["id"], work)
        total, errors = total + n, errors + e
    return total, errors


def _report_reminders(db, today: date) -> tuple[int, int]:
    offsets = _offsets(db, ("report_due_soon", "report_overdue"))
    total = errors = 0
    for rule in db.execute("SELECT * FROM report_due WHERE active=1 ORDER BY id").fetchall():
        def work(rule=rule):
            st, n = report_due.status(db, rule, today), 0
            if st["submitted"]:
                return 0
            due = st["due"].isoformat()
            if st["state"] in ("upcoming", "due"):
                for off in offsets["report_due_soon"]:
                    if off >= st["days"]:
                        n += notify.emit_report(db, "report_due_soon", rule["id"], None, period=st["period"], due=due, offset_days=off, days=st["days"])
            else:
                for off in offsets["report_overdue"]:
                    if off <= st["days"]:
                        n += notify.emit_report(db, "report_overdue", rule["id"], None, period=st["period"], due=due, offset_days=off, days=st["days"])
            return n
        n, e = _guarded(db, "report rule", rule["id"], work)
        total, errors = total + n, errors + e
    return total, errors


def run_daily(db, today: date | None = None, force: bool = False, backup_dir: str | None = None,
              now: datetime | None = None) -> dict:
    today, now = today or timeutil.today(), now or datetime.now()
    try:
        db.execute("INSERT INTO job_runs(job, run_date, started_at) VALUES('daily', ?, ?)", (today.isoformat(), fmt(now)))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        if not force:
            # มีรอบของวันนี้อยู่แล้ว: ทำต่อได้เฉพาะรอบที่ "เริ่มแล้วแต่ไม่เสร็จนานเกินกำหนด" (ล้มกลางทาง) — เปลี่ยนเวลาเริ่มแบบ atomic ให้เหลือผู้ชนะคนเดียว
            took = db.execute("UPDATE job_runs SET started_at=? WHERE job='daily' AND run_date=? AND finished_at IS NULL "
                              "AND (started_at IS NULL OR started_at <= ?)",
                              (fmt(now), today.isoformat(), fmt(now - timedelta(minutes=STALE_MINUTES)))).rowcount
            db.commit()
            if took != 1:
                return {"skipped": True}
    outbox_before = db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0]
    notifications = errors = 0
    rows = db.execute("SELECT id, due_date FROM hazards WHERE status IN ('reported','assessed','assigned') "
                      "AND due_date IS NOT NULL ORDER BY id").fetchall()
    offsets = {ev: sorted(r[0] for r in db.execute("SELECT DISTINCT offset_days FROM notify_rules WHERE event=? AND enabled=1", (ev,)))
               for ev in ("due_soon", "overdue")}
    for hazard_id, due_text in rows:
        try:
            due = date.fromisoformat(due_text)
            days = (due - today).days
            db.execute("SAVEPOINT daily_item")
            # ตามทันทุกขั้นที่ "ถึงแล้ว" (ข้ามวันที่ระบบดับก็ไม่พลาดการยกระดับ); ขั้นที่เคยส่งแล้วถูกกันซ้ำด้วย dedupe key ของกฎ+ขั้น
            if days >= 0:
                for off in offsets["due_soon"]:
                    if off >= days:
                        notifications += notify.emit(db, "due_soon", hazard_id, None, offset_days=off, days=days)
            else:
                late = -days
                for off in offsets["overdue"]:
                    if off <= late:
                        notifications += notify.emit(db, "overdue", hazard_id, None, offset_days=off, days=late)
            db.execute("RELEASE daily_item")
            db.commit()
        except Exception:                                  # noqa: BLE001 — รายการเดียวเสียไม่ทำให้ทั้งรอบล้ม
            errors += 1
            log.exception("daily reminder failed for hazard %s", hazard_id)
            try:
                db.execute("ROLLBACK TO daily_item"); db.execute("RELEASE daily_item")
            except sqlite3.Error:
                db.rollback()
    capa_notifications, capa_errors = _capa_reminders(db, today)
    patrol_notifications, patrol_errors = _patrol_reminders(db, today)
    report_notifications, report_errors = _report_reminders(db, today)
    errors += capa_errors + patrol_errors + report_errors
    outbox = db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] - outbox_before
    path = None
    try:
        db_path = db.execute("PRAGMA database_list").fetchone()[2]
        if db_path:
            path = backup_database(db_path, backup_dir or os.path.join(os.path.dirname(db_path), "backups"), today)
    except Exception:                                      # noqa: BLE001 — สำรองไม่สำเร็จต้องไม่ทำให้การเตือนเสีย
        log.exception("database backup failed")
    db.execute("UPDATE job_runs SET finished_at=datetime('now','localtime') WHERE job='daily' AND run_date=?", (today.isoformat(),))
    db.commit()
    return {"skipped": False, "notifications": notifications, "outbox": outbox, "errors": errors, "backup": path,
            "capa_notifications": capa_notifications, "patrol_notifications": patrol_notifications, "report_notifications": report_notifications}


def start_scheduler(app, interval: float = 60.0) -> threading.Thread:
    """ตรวจทุก interval วินาทีว่าถึงเวลางานประจำวันหรือยัง (รวมการตามทันตอนเริ่มระบบ)"""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                con = connect(app.config["DATABASE"])
                try:
                    if due_run_needed(con, datetime.now()):
                        run_daily(con)
                finally:
                    con.close()
            except Exception:                              # noqa: BLE001
                log.exception("scheduler iteration failed")
            stop.wait(interval)

    thread = threading.Thread(target=loop, name="safety-scheduler", daemon=True)
    thread.stop_event = stop
    thread.start()
    return thread
