"""safety/approvals.py — สายอนุมัติ: รายการอันตราย/อุบัติเหตุต้องได้รับอนุมัติเป็นขั้นจากผู้ที่ผูกกับหน่วยงานนั้น ก่อนปิดรายการได้

ค่าตั้งต้น (แก้ได้ที่ ผู้ดูแลระบบ › สายการอนุมัติ): ขั้น 1 = SV, ขั้น 2 = DM หรือ DGM (คนใดคนหนึ่งในขั้นพอ), แจ้งให้ทราบ = GM
- ผู้อนุมัติของขั้นหนึ่ง = ผู้ใช้ที่ใช้งานอยู่ ตำแหน่งตรงกับรหัสของขั้น และขอบเขตหน่วยงานครอบคลุมรายการ
- อนุมัติทีละขั้นตามลำดับ (ขั้น 2 เริ่มเมื่อขั้น 1 อนุมัติแล้ว); ผู้แจ้งอนุมัติรายการของตัวเองไม่ได้
- ผู้ดูแลระบบอนุมัติแทนได้ (ต้องระบุเหตุผล และบันทึกว่าเป็นการอนุมัติแทน) เพื่อไม่ให้งานค้างเมื่อหน่วยงานยังไม่มีผู้ดำรงตำแหน่ง
- ปิดอยู่ตั้งต้น (approval_enabled=0) — เปิดแล้วมีผลกับรายการที่แจ้งหลังจากนั้น รายการเดิมไม่ถูกบล็อก"""
from __future__ import annotations

import logging

from . import notify

log = logging.getLogger("safety.approvals")

KINDS = {"hazard": ("hazards", "รายการอันตราย", "/hazards/"), "incident": ("incidents", "รายการอุบัติเหตุ", "/incidents/")}
DEFAULTS = {"approval_enabled": "0", "approval_step1": "SV", "approval_step2": "DM,DGM", "approval_notify": "GM"}
STATUS_TH = {"pending": "รออนุมัติ", "approved": "อนุมัติแล้ว", "rejected": "ไม่อนุมัติ"}
COMMENT_MAX = 500


def _setting(db, key: str) -> str:
    row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else DEFAULTS[key]


def enabled(db) -> bool:
    return _setting(db, "approval_enabled") == "1"


def codes(db, key: str) -> list[str]:
    return [c.strip().upper() for c in _setting(db, key).split(",") if c.strip()]


def step_keys(db) -> list[int]:
    return [1, 2]


def step_codes(db, step: int) -> list[str]:
    return codes(db, f"approval_step{step}")


def position_names(db, code_list: list[str]) -> str:
    names = []
    for c in code_list:
        row = db.execute("SELECT name_th FROM positions WHERE upper(code)=?", (c,)).fetchone()
        names.append(row[0] if row else c)
    return " หรือ ".join(names) if names else "(ยังไม่ได้กำหนดตำแหน่ง)"


def save_config(db, enabled_flag: bool, step1: list[str], step2: list[str], notify_codes: list[str]) -> list[str]:
    known = {r[0].upper() for r in db.execute("SELECT code FROM positions WHERE active=1")}
    errors = []
    if not step1 or not step2:
        errors.append("ต้องเลือกตำแหน่งผู้อนุมัติอย่างน้อย 1 ตำแหน่งในแต่ละขั้น")
    for label, lst in (("ขั้นที่ 1", step1), ("ขั้นที่ 2", step2), ("ผู้ที่ต้องแจ้งให้ทราบ", notify_codes)):
        if any(c.upper() not in known for c in lst):
            errors.append(f"{label}: เลือกได้เฉพาะตำแหน่งที่ใช้งานอยู่")
    if errors:
        return errors
    for key, value in (("approval_enabled", "1" if enabled_flag else "0"), ("approval_step1", ",".join(c.upper() for c in step1)),
                       ("approval_step2", ",".join(c.upper() for c in step2)), ("approval_notify", ",".join(c.upper() for c in notify_codes))):
        db.execute("INSERT INTO app_settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    db.commit()
    return []


# ---------- ตำแหน่ง/หน่วยงาน ----------
def position_code(db, user) -> str:
    if user is None or user["position_id"] is None:
        return ""
    row = db.execute("SELECT code FROM positions WHERE id=?", (user["position_id"],)).fetchone()
    return (row[0] if row else "").upper()


def rank_map(db) -> dict[str, int]:
    """ลำดับตำแหน่งจากต่ำไปสูงตามลำดับในรายการตำแหน่ง (Operator → … → GM)"""
    return {r[0].upper(): i for i, r in enumerate(db.execute("SELECT code FROM positions ORDER BY id"))}


def load_record(db, kind: str, ref_id: int):
    table = KINDS[kind][0]
    return db.execute(f"SELECT * FROM {table} WHERE id=?", (ref_id,)).fetchone()


def unit_of(db, rec) -> tuple[int | None, int | None]:
    """(แผนก, โรงงาน) ของรายการ ผ่านพื้นที่ → ไลน์ → แผนก"""
    row = db.execute("SELECT d.id, d.factory_id FROM risk_areas ra JOIN lines l ON l.id = ra.line_id JOIN departments d ON d.id = l.department_id "
                     "WHERE ra.id=?", (rec["risk_area_id"],)).fetchone()
    return (row[0], row[1]) if row else (None, None)


def unit_users(db, rec) -> list:
    """ทุกคนที่ผูกกับหน่วยงานของรายการ: แผนกเดียวกัน หรือผู้ที่ผูกกับโรงงานเดียวกัน (ไม่ระบุแผนก)"""
    dept, factory = unit_of(db, rec)
    if dept is None:
        return []
    return db.execute("SELECT * FROM safety_users WHERE active=1 AND pending=0 AND (department_id=? OR (department_id IS NULL AND factory_id=?)) ORDER BY id",
                      (dept, factory)).fetchall()


def users_with_positions(db, code_list: list[str], rec) -> list:
    if not code_list:
        return []
    marks = ",".join("?" for _ in code_list)
    users = db.execute(f"SELECT u.* FROM safety_users u JOIN positions p ON p.id = u.position_id "
                       f"WHERE u.active=1 AND u.pending=0 AND upper(p.code) IN ({marks}) ORDER BY u.id", [c.upper() for c in code_list]).fetchall()
    return [u for u in users if notify._covers(db, u, rec)]


def approvers_for(db, rec, step: int) -> list:
    return users_with_positions(db, step_codes(db, step), rec)


# ---------- สถานะ ----------
def rows(db, kind: str, ref_id: int) -> list:
    return db.execute("SELECT a.*, u.name AS approver_name FROM approvals a LEFT JOIN safety_users u ON u.id = a.approver_id "
                      "WHERE a.kind=? AND a.ref_id=? ORDER BY a.step", (kind, ref_id)).fetchall()


def summary(db, kind: str, ref_id: int) -> dict | None:
    rs = rows(db, kind, ref_id)
    if not rs:
        return None
    pending = [r for r in rs if r["status"] == "pending"]
    rejected = [r for r in rs if r["status"] == "rejected"]
    state = "rejected" if rejected else ("pending" if pending else "approved")
    return {"rows": rs, "state": state, "state_th": {"rejected": "ไม่ได้รับอนุมัติ", "pending": "รออนุมัติ", "approved": "อนุมัติครบแล้ว"}[state],
            "next_step": pending[0]["step"] if pending and not rejected else None}


def gate_errors(db, kind: str, ref_id: int) -> list[str]:
    """ปิดรายการไม่ได้จนกว่าจะอนุมัติครบ (เฉพาะรายการที่เปิดสายอนุมัติไว้ตอนแจ้ง)"""
    s = summary(db, kind, ref_id)
    if s is None or s["state"] == "approved":
        return []
    if s["state"] == "rejected":
        return ["รายการนี้ไม่ได้รับอนุมัติ — ต้องขออนุมัติใหม่ก่อนจึงจะปิดรายการได้"]
    waiting = " → ".join(f"ขั้น {r['step']} ({position_names(db, step_codes(db, r['step']))})" for r in s["rows"] if r["status"] == "pending")
    return [f"ต้องได้รับอนุมัติครบก่อนปิดรายการ — รอ: {waiting}"]


def can_decide(db, user, kind: str, rec, step: int) -> str | None:
    """คืนชนิดสิทธิ์ ('own' = ตำแหน่งตรง, 'proxy' = ผู้ดูแลระบบอนุมัติแทน) หรือ None"""
    if not enabled(db) or user is None:
        return None
    if user["id"] == rec["reported_by"]:
        return None
    if position_code(db, user) in step_codes(db, step) and notify._covers(db, user, rec):
        return "own"
    if user["role"] == "admin":
        return "proxy"
    return None


def current_step(db, kind: str, ref_id: int) -> int | None:
    s = summary(db, kind, ref_id)
    return s["next_step"] if s else None


def pending_for(db, user) -> list[dict]:
    """งานที่รอคนนี้ตัดสินใจ (ขั้นปัจจุบันของแต่ละรายการ) — admin เห็นทั้งหมดในฐานะผู้อนุมัติแทน"""
    out = []
    for r in db.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY ref_id, step").fetchall():
        if r["kind"] not in KINDS or current_step(db, r["kind"], r["ref_id"]) != r["step"]:
            continue
        rec = load_record(db, r["kind"], r["ref_id"])
        if rec is None:
            continue
        mode = can_decide(db, user, r["kind"], rec, r["step"])
        if mode:
            out.append({"kind": r["kind"], "ref_id": r["ref_id"], "step": r["step"], "mode": mode, "rec": rec,
                        "step_label": position_names(db, step_codes(db, r["step"]))})
    return out


# ---------- ขั้นตอน ----------
def request(db, kind: str, ref_id: int, actor_id: int | None) -> int:
    """เปิดสายอนุมัติให้รายการใหม่ (ถ้าเปิดใช้) แล้วแจ้งผู้อนุมัติขั้นที่ 1; ไม่ commit, ไม่ throw"""
    try:
        if not enabled(db) or kind not in KINDS or db.execute("SELECT 1 FROM approvals WHERE kind=? AND ref_id=?", (kind, ref_id)).fetchone():
            return 0
        for step in (1, 2):
            db.execute("INSERT INTO approvals(kind, ref_id, step) VALUES(?,?,?)", (kind, ref_id, step))
        return notify.emit_approval(db, "approval_requested", kind, ref_id, actor_id, step=1)
    except Exception:                                      # noqa: BLE001 — สายอนุมัติต้องไม่ทำให้การแจ้งล้ม
        log.exception("approvals.request(%s,%s) failed", kind, ref_id)
        return 0


def decide(db, user, kind: str, ref_id: int, step: int, approve: bool, comment: str) -> list[str]:
    if kind not in KINDS:
        return ["ไม่พบรายการ"]
    rec = load_record(db, kind, ref_id)
    row = db.execute("SELECT * FROM approvals WHERE kind=? AND ref_id=? AND step=?", (kind, ref_id, step)).fetchone()
    if rec is None or row is None:
        return ["ไม่พบรายการอนุมัติ"]
    mode = can_decide(db, user, kind, rec, step)
    if mode is None:
        return ["คุณไม่มีสิทธิ์อนุมัติขั้นนี้ของรายการนี้ (ต้องเป็นผู้ดำรงตำแหน่งที่กำหนดในหน่วยงานเดียวกัน และไม่ใช่ผู้แจ้งเอง)"]
    comment = (comment or "").strip()
    if len(comment) > COMMENT_MAX:
        return [f"ความเห็นยาวเกิน {COMMENT_MAX} ตัวอักษร"]
    if (not approve or mode == "proxy") and not comment:
        return ["กรุณาระบุเหตุผล" + (" (อนุมัติแทนต้องระบุเหตุผลเสมอ)" if approve else "ที่ไม่อนุมัติ")]
    if current_step(db, kind, ref_id) != step or row["status"] != "pending":
        return ["รายการนี้ไม่ได้รออนุมัติขั้นนี้ (อาจมีผู้อื่นตัดสินใจไปแล้ว หรือขั้นก่อนหน้ายังไม่เสร็จ)"]
    cur = db.execute("UPDATE approvals SET status=?, approver_id=?, proxy=?, comment=?, decided_at=datetime('now','localtime') "
                     "WHERE id=? AND status='pending'", ("approved" if approve else "rejected", user["id"], 1 if mode == "proxy" else 0, comment, row["id"]))
    if cur.rowcount != 1:
        db.rollback()
        return ["รายการนี้มีผู้อื่นตัดสินใจไปแล้ว"]
    event_kind = "hazard" if kind == "hazard" else "incident"
    if not approve:
        notify.emit_approval(db, "approval_rejected", kind, ref_id, user["id"], step=step)
    elif step < max(step_keys(db)):
        notify.emit_approval(db, "approval_requested", kind, ref_id, user["id"], step=step + 1)
    else:
        notify.emit_approval(db, "approval_completed", kind, ref_id, user["id"], step=step)
    _log_event(db, kind, ref_id, user["id"], approve, step, mode, comment)
    db.commit()
    return []


def _log_event(db, kind, ref_id, actor_id, approve, step, mode, comment) -> None:
    if kind == "hazard":
        from .hazards import add_event
        status = db.execute("SELECT status FROM hazards WHERE id=?", (ref_id,)).fetchone()[0]
        text = f"{'อนุมัติ' if approve else 'ไม่อนุมัติ'}ขั้น {step}{' (อนุมัติแทน)' if mode == 'proxy' else ''}" + (f": {comment}" if comment else "")
        add_event(db, ref_id, actor_id, "approval", status, status, text)


def restart(db, user, kind: str, ref_id: int) -> list[str]:
    """เริ่มสายอนุมัติใหม่หลังถูกไม่อนุมัติ — ผู้ดูแลระบบหรือผู้ที่ประเมิน/สอบสวนรายการได้"""
    if kind not in KINDS or load_record(db, kind, ref_id) is None:
        return ["ไม่พบรายการ"]
    s = summary(db, kind, ref_id)
    if s is None or s["state"] != "rejected":
        return ["ขอใหม่ได้เฉพาะรายการที่ไม่ได้รับอนุมัติ"]
    from .perms import user_permissions
    perms = user_permissions(db, user)
    if not (perms & {"users.manage", "hazard.assess", "incident.investigate"}):
        return ["ไม่มีสิทธิ์ขออนุมัติใหม่"]
    db.execute("UPDATE approvals SET status='pending', approver_id=NULL, proxy=0, comment='', decided_at=NULL WHERE kind=? AND ref_id=?", (kind, ref_id))
    notify.emit_approval(db, "approval_requested", kind, ref_id, user["id"], step=1)
    db.commit()
    return []


def org_matrix(db, min_code: str = "FM") -> list[dict]:
    """ผู้ใช้ตำแหน่งตั้งแต่ FM ขึ้นไป จัดกลุ่มตามหน่วยงาน (แผนก) เรียงจากต่ำไปสูง — ให้ผู้ดูแลตรวจสายอนุมัติของแต่ละหน่วยงาน
    ผู้ที่ผูกระดับโรงงาน/ทั้งบริษัท (เช่น DGM, GM) แสดงในทุกแผนกที่ตนครอบคลุม พร้อมบอกขอบเขต"""
    ranks = rank_map(db)
    floor = ranks.get(min_code.upper(), 0)
    users = [u for u in db.execute(
        "SELECT u.id, u.name, u.factory_id, u.department_id, p.code AS pcode, p.name_th AS pname FROM safety_users u "
        "JOIN positions p ON p.id = u.position_id WHERE u.active=1 AND u.pending=0").fetchall() if ranks.get(u["pcode"].upper(), -1) >= floor]
    out = []
    for d in db.execute("SELECT d.id, d.name_th, d.factory_id, f.name_th AS fname FROM departments d JOIN factories f ON f.id = d.factory_id "
                        "WHERE d.active=1 AND f.active=1 ORDER BY f.code, d.code").fetchall():
        members = []
        for u in users:
            if u["department_id"] == d["id"]:
                scope = "ประจำแผนก"
            elif u["department_id"] is None and u["factory_id"] == d["factory_id"]:
                scope = "ระดับโรงงาน"
            elif u["department_id"] is None and u["factory_id"] is None:
                scope = "ทั้งบริษัท"
            else:
                continue
            members.append({"id": u["id"], "name": u["name"], "position": u["pname"], "code": u["pcode"].upper(),
                            "rank": ranks.get(u["pcode"].upper(), 0), "scope": scope})
        members.sort(key=lambda x: (x["rank"], x["name"]))
        present = {m["code"] for m in members}
        missing = [f"ขั้น {st} ({position_names(db, step_codes(db, st))})" for st in step_keys(db) if not (present & set(step_codes(db, st)))]
        notify_missing = bool(codes(db, "approval_notify")) and not (present & set(codes(db, "approval_notify")))
        out.append({"label": f"{d['fname']} › {d['name_th']}", "users": members, "missing": missing, "notify_missing": notify_missing})
    return out


def card(db, user, kind: str, rec, perms: set[str]) -> dict | None:
    """ข้อมูลการ์ด «การอนุมัติ» บนหน้ารายละเอียด (None = ไม่มีสายอนุมัติสำหรับรายการนี้)"""
    s = summary(db, kind, rec["id"])
    if s is None:
        return None
    step = s["next_step"]
    mode = can_decide(db, user, kind, rec, step) if step else None
    return {**s, "kind": kind, "ref_id": rec["id"], "status_th": STATUS_TH, "my_step": step if mode else None, "my_mode": mode,
            "labels": {r["step"]: position_names(db, step_codes(db, r["step"])) for r in s["rows"]},
            "can_restart": bool(perms & {"users.manage", "hazard.assess", "incident.investigate"})}
