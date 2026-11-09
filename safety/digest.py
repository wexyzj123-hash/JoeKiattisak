"""safety/digest.py — สรุปงานค้างรายวันรายบุคคล (อีเมล / Teams): ส่งวันละหนึ่งข้อความต่อคนเมื่อมีงานค้างหรือต้องติดตาม
รวมเฉพาะสิ่งที่คนนั้นต้องทำจริง: งานที่ได้รับมอบหมายและเกินกำหนด รายการรอประเมิน/มอบหมาย/ยืนยัน และคำขอบัญชีที่รออนุมัติ
ไม่ส่งถ้าไม่มีอะไรค้าง ไม่ส่งซ้ำในวันเดียวกัน (dedupe key ต่อคน+วัน) และไม่มีข้อมูลบุคคลของผู้บาดเจ็บ/รหัสผ่านในข้อความ"""
from __future__ import annotations

import logging
from datetime import date

from .capa import scope_sql as capa_scope
from .hazards import hazard_scope_sql
from .incidents import scope_sql as incident_scope
from .notify import BODY_MAX, TITLE_MAX, _queue_external, clean, valid_contact
from .perms import user_permissions
from .schema import ACTIVE_OUTBOX

log = logging.getLogger("safety.digest")


def enabled(db) -> bool:
    row = db.execute("SELECT value FROM app_settings WHERE key='backlog_digest'").fetchone()
    return (row[0] if row else "1") == "1"


def backlog(db, user, today: date) -> list[tuple[str, int, str]]:
    """[(หัวข้อ, จำนวน, ลิงก์)] ของสิ่งที่ผู้ใช้คนนี้มีค้าง (เฉพาะที่จำนวน > 0)"""
    perms, t, uid = user_permissions(db, user), today.isoformat(), user["id"]
    out: list[tuple[str, int, str]] = []
    n = lambda q, a=(): db.execute(q, a).fetchone()[0]
    mine = n("SELECT COUNT(*) FROM hazards WHERE owner_id=? AND status='assigned'", (uid,))
    late = n("SELECT COUNT(*) FROM hazards WHERE owner_id=? AND status='assigned' AND due_date < ?", (uid, t))
    if late:
        out.append(("รายการอันตรายที่ได้รับมอบหมายและเกินกำหนด", late, "/hazards?mine=1&overdue=1"))
    if mine - late > 0:
        out.append(("รายการอันตรายที่ได้รับมอบหมาย (ยังไม่เกินกำหนด)", mine - late, "/hazards?mine=1"))
    hs, ha = hazard_scope_sql(user)
    if "hazard.assess" in perms:
        c = n(f"SELECT COUNT(*) FROM hazards h WHERE h.status='reported' AND {hs}", ha)
        if c:
            out.append(("รายการอันตรายที่รอประเมินความเสี่ยง", c, "/hazards?status=reported"))
    if "hazard.assign" in perms:
        c = n(f"SELECT COUNT(*) FROM hazards h WHERE h.status='assessed' AND {hs}", ha)
        if c:
            out.append(("รายการอันตรายที่ประเมินแล้วรอมอบหมาย", c, "/hazards?status=assessed"))
    c = n("SELECT COUNT(*) FROM incidents WHERE owner_id=? AND status IN ('investigating','actions_set') AND due_date < ?", (uid, t))
    if c:
        out.append(("การสอบสวนอุบัติเหตุที่เกินกำหนด", c, "/incidents"))
    if "incident.investigate" in perms:
        isql, ia = incident_scope(user)
        c = n(f"SELECT COUNT(*) FROM incidents i WHERE i.status='reported' AND {isql}", ia)
        if c:
            out.append(("อุบัติเหตุ/Near Miss ที่รอมอบหมายผู้สอบสวน", c, "/incidents"))
    mine = n("SELECT COUNT(*) FROM capas WHERE owner_id=? AND status IN ('open','in_progress')", (uid,))
    late = n("SELECT COUNT(*) FROM capas WHERE owner_id=? AND status IN ('open','in_progress') AND due_date < ?", (uid, t))
    if late:
        out.append(("CAPA ที่เกินกำหนด", late, "/capas?mine=1&overdue=1"))
    if mine - late > 0:
        out.append(("CAPA ที่ต้องดำเนินการ (ยังไม่เกินกำหนด)", mine - late, "/capas?mine=1"))
    if "capa.verify" in perms:
        csql, ca = capa_scope(user)
        c = n(f"SELECT COUNT(*) FROM capas c WHERE c.status='done' AND c.owner_id <> ? AND c.done_by IS NOT ? AND {csql}", [uid, uid] + ca)
        if c:
            out.append(("CAPA ที่รอยืนยันผล", c, "/capas?status=done"))
    if "users.manage" in perms:
        c = n("SELECT COUNT(*) FROM safety_users WHERE pending=1")
        if c:
            out.append(("คำขอสร้างบัญชีที่รออนุมัติ", c, "/admin/users#pending"))
    return out


def queue_all(db, today: date) -> int:
    """เข้าคิวสรุปงานค้างให้ผู้ใช้ที่เลือกรับทางอีเมล/Teams และมีงานค้าง; คืนจำนวนข้อความที่เข้าคิว (ไม่ commit)"""
    if not enabled(db):
        return 0
    queued = 0
    for user in db.execute("SELECT * FROM safety_users WHERE active=1 AND pending=0 ORDER BY id").fetchall():
        opted = set((user["notify_channels"] or "").split(","))
        targets = [(ch, valid_contact(ch, user)) for ch in ACTIVE_OUTBOX if ch in opted]
        targets = [(ch, t) for ch, t in targets if t]
        if not targets:
            continue
        try:
            items = backlog(db, user, today)
        except Exception:                                  # noqa: BLE001 — ผู้ใช้คนเดียวเสียไม่ทำให้ทั้งรอบล้ม
            log.exception("digest failed for user %s", user["id"])
            continue
        if not items:
            continue
        total = sum(c for _, c, _ in items)
        title = clean(f"สรุปงานค้างของคุณ {today.strftime('%d/%m/%Y')} — {total} รายการ", TITLE_MAX)
        body = clean(" · ".join(f"{label} {c}" for label, c, _ in items) + " — เข้าสู่ระบบ AIAT Safety เพื่อดำเนินการ", BODY_MAX)
        for ch, target in targets:
            before = db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0]
            _queue_external(db, ch, target, title, body, 0, None, f"digest:{user['id']}:{today.isoformat()}")
            queued += db.execute("SELECT COUNT(*) FROM notify_outbox").fetchone()[0] - before
    return queued
