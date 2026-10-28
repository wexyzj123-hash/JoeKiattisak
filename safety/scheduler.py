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
from datetime import date, datetime

from . import notify, timeutil
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


def due_run_needed(db, now: datetime) -> bool:
    """ถึงเวลาแล้ว (หรือเลยเวลาแล้ว = ตามทัน) และวันนี้ยังไม่เคยรัน"""
    hour, minute = _daily_time(db)
    if (now.hour, now.minute) < (hour, minute):
        return False
    return db.execute("SELECT 1 FROM job_runs WHERE job='daily' AND run_date=?", (now.date().isoformat(),)).fetchone() is None


def run_daily(db, today: date | None = None, force: bool = False, backup_dir: str | None = None) -> dict:
    today = today or timeutil.today()
    try:
        db.execute("INSERT INTO job_runs(job, run_date) VALUES('daily', ?)", (today.isoformat(),))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        if not force:
            return {"skipped": True}
    outbox_before = db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0]
    notifications = errors = 0
    rows = db.execute("SELECT id, due_date FROM hazards WHERE status IN ('reported','assessed','assigned') "
                      "AND due_date IS NOT NULL ORDER BY id").fetchall()
    for hazard_id, due_text in rows:
        try:
            due = date.fromisoformat(due_text)
            days = (due - today).days
            event, offset = ("due_soon", days) if days >= 0 else ("overdue", -days)
            db.execute("SAVEPOINT daily_item")
            notifications += notify.emit(db, event, hazard_id, None, offset_days=offset)
            db.execute("RELEASE daily_item")
            db.commit()
        except Exception:                                  # noqa: BLE001 — รายการเดียวเสียไม่ทำให้ทั้งรอบล้ม
            errors += 1
            log.exception("daily reminder failed for hazard %s", hazard_id)
            try:
                db.execute("ROLLBACK TO daily_item"); db.execute("RELEASE daily_item")
            except sqlite3.Error:
                db.rollback()
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
    return {"skipped": False, "notifications": notifications, "outbox": outbox, "errors": errors, "backup": path}


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
