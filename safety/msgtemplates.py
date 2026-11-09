"""safety/msgtemplates.py — ข้อความแจ้งเตือนที่ผู้ดูแลระบบแต่งเองได้ (ชื่อเรื่อง + เนื้อหา ต่อเหตุการณ์)

ไวยากรณ์ของแม่แบบ (ข้อความธรรมดา ไม่มี HTML):
- {ชื่อตัวแปร}  แทนด้วยค่าจริง เช่น {code} {title} {area} — ตัวแปรที่ใช้ได้ขึ้นกับเหตุการณ์ (ดูในหน้าตั้งค่า)
- [[ ... ]]  ส่วนเสริม: ถ้ามีตัวแปรข้างในที่ว่าง จะตัดทั้งส่วนทิ้ง เช่น [[ · ครบกำหนด {due}]]
- ขึ้นบรรทัดใหม่ได้ในเนื้อหา (อีเมล/Teams/หน้าแจ้งเตือนแสดงตามบรรทัดจริง)
ข้อความมาตรฐานของระบบอยู่ใน DEFAULTS ด้านล่าง — ผู้ดูแลแก้แล้วเก็บในตาราง notify_templates; กดคืนค่ามาตรฐานเพื่อลบที่แก้"""
from __future__ import annotations

import re

SIGN = "จึงเรียนมาเพื่อโปรดทราบ ขอแสดงความนับถือ — ระบบ AIAT Safety"
TITLE_LIMIT, BODY_LIMIT = 150, 1500
PH_RE = re.compile(r"\{([a-z_]+)\}")
OPT_RE = re.compile(r"\[\[(.*?)\]\]", re.S)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# กลุ่มตัวแปร: ชื่อ → คำอธิบาย
VARS = {
    "hazard": {"code": "เลขที่รายการ", "title": "หัวข้อ", "area": "พื้นที่/ไลน์", "level": "ระดับความเสี่ยง", "score": "คะแนนความเสี่ยง", "due": "วันครบกำหนด",
               "days": "จำนวนวัน (ใกล้ครบ/เกิน)", "reason": "เหตุผลที่ปฏิเสธ", "note": "ผลการแก้ไข", "reporter": "ผู้แจ้ง", "owner": "ผู้รับผิดชอบ", "location": "สถานที่"},
    "incident": {"code": "เลขที่รายการ", "title": "หัวข้อ", "kind": "ประเภท (อุบัติเหตุ/Near Miss)", "area": "พื้นที่/ไลน์", "due": "วันครบกำหนด",
                 "when": "วันเวลาที่เกิดเหตุ", "reporter": "ผู้แจ้ง", "owner": "ผู้สอบสวน"},
    "capa": {"code": "เลขที่ CAPA", "title": "หัวข้อ", "area": "พื้นที่/ไลน์", "due": "วันครบกำหนด", "days": "จำนวนวัน (ใกล้ครบ/เกิน)"},
    "patrol": {"name": "ชื่อแบบตรวจ", "area": "พื้นที่/ไลน์", "due": "วันครบกำหนด", "days": "จำนวนวันที่เลย"},
    "report": {"name": "ชื่อรายงาน", "period": "งวด", "department": "แผนก", "due": "วันครบกำหนด", "days": "จำนวนวัน (ใกล้ครบ/เกิน)"},
    "user": {"name": "ชื่อผู้ขอ", "username": "ชื่อผู้ใช้", "org": "สังกัดที่ระบุ", "position": "ตำแหน่ง"},
    "unit": {"kind": "ประเภท (อันตราย/อุบัติเหตุ/Near Miss)", "code": "เลขที่รายการ", "title": "หัวข้อ", "area": "พื้นที่/ไลน์", "when": "วันเวลา", "reporter": "ผู้แจ้ง"},
    "approval": {"kind": "ประเภทรายการ", "code": "เลขที่รายการ", "title": "หัวข้อ", "step": "ขั้นที่", "approvers": "ตำแหน่งผู้อนุมัติของขั้น", "reason": "เหตุผล (กรณีไม่อนุมัติ)"},
}
GROUP_OF = {"hazard_reported": "hazard", "hazard_assessed": "hazard", "hazard_assigned": "hazard", "hazard_closed": "hazard", "hazard_rejected": "hazard",
            "due_soon": "hazard", "overdue": "hazard",
            "incident_reported": "incident", "incident_assigned": "incident", "incident_closed": "incident",
            "capa_assigned": "capa", "capa_done": "capa", "capa_verified": "capa", "capa_reopened": "capa", "capa_due_soon": "capa", "capa_overdue": "capa",
            "patrol_due": "patrol", "patrol_overdue": "patrol", "report_due_soon": "report", "report_overdue": "report", "user_requested": "user",
            "unit_hazard_notice": "unit", "unit_incident_notice": "unit",
            "approval_requested": "approval", "approval_completed": "approval", "approval_rejected": "approval"}

_HZ_OPT = "[[ · ระดับ {level} ({score})]]"
DEFAULTS: dict[str, tuple[str, str]] = {
    "hazard_reported": ("แจ้งอันตรายใหม่ {code}", "{title}[[ · พื้นที่ {area}]]"),
    "hazard_assessed": ("ประเมินความเสี่ยง {code}", "{title}" + _HZ_OPT + "[[ · พื้นที่ {area}]]"),
    "hazard_assigned": ("มอบหมายงานแก้ไข {code}", "{title}[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "hazard_closed": ("ปิดรายการอันตราย {code}", "{title} · ผลการแก้ไข: {note}"),
    "hazard_rejected": ("รายการถูกปฏิเสธ {code}", "{title} · เหตุผล: {reason}"),
    "due_soon": ("ใกล้ครบกำหนด {code} (อีก {days} วัน)", "{title}" + _HZ_OPT + "[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "overdue": ("เกินกำหนด {code} ({days} วัน)", "{title}" + _HZ_OPT + "[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "incident_reported": ("แจ้ง{kind}ใหม่ {code}", "{title}[[ · พื้นที่ {area}]]"),
    "incident_assigned": ("มอบหมายสอบสวน{kind} {code}", "{title}[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "incident_closed": ("ปิดรายการ{kind} {code}", "{title}[[ · พื้นที่ {area}]]"),
    "capa_assigned": ("มอบหมาย CAPA {code}", "{title}[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "capa_done": ("CAPA ดำเนินการเสร็จ รอยืนยัน {code}", "{title}[[ · พื้นที่ {area}]]"),
    "capa_verified": ("CAPA ยืนยันผลแล้ว {code}", "{title}[[ · พื้นที่ {area}]]"),
    "capa_reopened": ("CAPA ถูกตีกลับให้แก้ไข {code}", "{title}[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "capa_due_soon": ("CAPA ใกล้ครบกำหนด {code} (อีก {days} วัน)", "{title}[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "capa_overdue": ("CAPA เกินกำหนด {code} ({days} วัน)", "{title}[[ · ครบกำหนด {due}]][[ · พื้นที่ {area}]]"),
    "patrol_due": ("ถึงรอบตรวจความปลอดภัย: {name}", "ครบกำหนด {due}[[ · พื้นที่ {area}]]"),
    "patrol_overdue": ("เลยรอบตรวจความปลอดภัย {days} วัน: {name}", "ครบกำหนด {due}[[ · พื้นที่ {area}]]"),
    "report_due_soon": ("ใกล้ครบกำหนดส่งรายงาน (อีก {days} วัน): {name}", "งวด {period}[[ · แผนก {department}]] · ครบกำหนด {due}"),
    "report_overdue": ("เกินกำหนดส่งรายงาน {days} วัน: {name}", "งวด {period}[[ · แผนก {department}]] · ครบกำหนด {due}"),
    "user_requested": ("คำขอสร้างบัญชีใหม่: {name}", "{username} · {org} · {position} — รอผู้ดูแลระบบอนุมัติ"),
    "unit_hazard_notice": ("[แจ้งเพื่อทราบ] พบอันตรายในหน่วยงาน {code}",
                           "เรียน ผู้เกี่ยวข้อง\nขอแจ้งให้ทราบว่า มีการรายงานอันตราย[[ ณ {area}]] เรื่อง \"{title}\" (เลขที่ {code}) เมื่อ {when} "
                           "ขณะนี้อยู่ระหว่างการประเมินความเสี่ยงและดำเนินการแก้ไข โปรดใช้ความระมัดระวังและแจ้งผู้ดูแลหากพบเหตุเพิ่มเติม\n" + SIGN),
    "unit_incident_notice": ("[แจ้งเพื่อทราบ] เกิด{kind}ในหน่วยงาน {code}",
                             "เรียน ผู้เกี่ยวข้อง\nขอแจ้งให้ทราบว่า ได้เกิดเหตุ{kind}[[ ณ {area}]] เรื่อง \"{title}\" (เลขที่ {code}) เมื่อ {when} "
                             "ขณะนี้อยู่ระหว่างการสอบสวนและดำเนินการแก้ไข โปรดติดตามความคืบหน้าและให้ความร่วมมือกับผู้เกี่ยวข้อง\n" + SIGN),
    "approval_requested": ("[ขออนุมัติ] {kind} {code} (ขั้นที่ {step}: {approvers})",
                           "เรียน ผู้อนุมัติ ({approvers})\nขอเรียนเชิญพิจารณาอนุมัติ{kind} เลขที่ {code} เรื่อง \"{title}\" ตามสายการอนุมัติขั้นที่ {step} "
                           "โดยเข้าที่เมนู «งานรออนุมัติ» ในระบบ AIAT Safety\n" + SIGN),
    "approval_completed": ("[แจ้งผลการอนุมัติ] {kind} {code} ได้รับอนุมัติครบทุกขั้น",
                           "เรียน ผู้เกี่ยวข้อง\nขอแจ้งว่า{kind} เลขที่ {code} เรื่อง \"{title}\" ได้รับการอนุมัติครบทุกขั้นตอนแล้ว "
                           "สามารถดำเนินการปิดรายการได้เมื่อการแก้ไขเสร็จสมบูรณ์\n" + SIGN),
    "approval_rejected": ("[แจ้งผลการอนุมัติ] {kind} {code} ไม่ได้รับอนุมัติ",
                          "เรียน ผู้เกี่ยวข้อง\nขอแจ้งว่า{kind} เลขที่ {code} เรื่อง \"{title}\" ไม่ได้รับอนุมัติในขั้นที่ {step}[[ เนื่องจาก {reason}]] "
                          "โปรดตรวจสอบและดำเนินการตามที่เห็นสมควร\n" + SIGN),
}
SAMPLES = {"code": "HZ-2611-0001", "title": "น้ำมันรั่วบนพื้นหน้าเครื่องปั๊ม", "area": "โรงงานบางนา › ฝ่ายผลิต › ไลน์ประกอบ 1", "level": "B2 หยุดงาน", "score": "14",
           "due": "2026-11-20", "days": "3", "reason": "ข้อมูลไม่ครบถ้วน", "note": "ทำความสะอาดและซ่อมซีลเรียบร้อย", "reporter": "สมชาย ใจดี", "owner": "วิภา รักษ์ปลอดภัย",
           "location": "หน้าเครื่องปั๊ม 3", "kind": "อุบัติเหตุ", "when": "2026-11-03 14:10", "name": "แบบตรวจประจำสัปดาห์", "period": "2026-10", "department": "ฝ่ายผลิต",
           "username": "somying.k", "org": "โรงงานบางนา › ฝ่ายผลิต", "position": "LL", "step": "1", "approvers": "SV"}
EVENT_SAMPLES = {"incident_reported": {"code": "IN-2611-0001"}, "unit_hazard_notice": {"kind": "อันตราย"},
                 "approval_requested": {"kind": "รายการอันตราย"}, "approval_completed": {"kind": "รายการอันตราย"}, "approval_rejected": {"kind": "รายการอันตราย"}}


def allowed(event: str) -> dict:
    return VARS[GROUP_OF[event]]


def _clean_value(value, limit=200) -> str:
    from .notify import clean
    return clean(value, limit)


def fill(template: str, values: dict) -> str:
    """แทนตัวแปรและตัดส่วนเสริมที่มีตัวแปรว่าง; ตัวแปรที่ไม่รู้จักคงข้อความเดิม (ไม่ throw)"""
    def optional(m):
        inner = m.group(1)
        names = PH_RE.findall(inner)
        return "" if any(not str(values.get(n, "")).strip() for n in names) else PH_RE.sub(lambda x: str(values.get(x.group(1), "")), inner)
    out = OPT_RE.sub(optional, template)
    return PH_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), out)


def clean_text(text, limit: int) -> str:
    """ข้อความหลายบรรทัด: ตัดอักขระควบคุม (เว้นขึ้นบรรทัดใหม่) ลดช่องว่างซ้ำ ไม่เกิน 1 บรรทัดว่างติดกัน และจำกัดความยาว"""
    text = CTRL_RE.sub(" ", ("" if text is None else str(text)).replace("\r\n", "\n").replace("\r", "\n"))
    lines = [re.sub(r"[ \t   ]+", " ", ln).strip() for ln in text.split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out[:limit]


def render(title_tpl: str, body_tpl: str, values: dict) -> tuple[str, str]:
    vals = {k: _clean_value(v, 300 if k in ("note", "reason") else 200) for k, v in values.items()}
    from .notify import clean
    return clean(fill(title_tpl, vals), TITLE_LIMIT), clean_text(fill(body_tpl, vals), BODY_LIMIT)


def get(db, event: str) -> tuple[str, str]:
    """แม่แบบที่ใช้จริงของเหตุการณ์: ที่ผู้ดูแลแก้ไว้ ถ้าไม่มีใช้ข้อความมาตรฐาน"""
    if db is not None:
        row = db.execute("SELECT title, body FROM notify_templates WHERE event=?", (event,)).fetchone()
        if row is not None:
            return row["title"], row["body"]
    return DEFAULTS.get(event, ("แจ้งเตือน {code}", "{title}"))


def is_custom(db, event: str) -> bool:
    return db.execute("SELECT 1 FROM notify_templates WHERE event=?", (event,)).fetchone() is not None


def validate(event: str, title: str, body: str) -> list[str]:
    errors = []
    if event not in DEFAULTS:
        return ["ไม่พบเหตุการณ์"]
    ok = set(allowed(event))
    if not title.strip():
        errors.append("กรุณากรอกชื่อเรื่อง")
    if len(title) > TITLE_LIMIT * 2:
        errors.append(f"ชื่อเรื่องยาวเกินไป (เกิน {TITLE_LIMIT * 2} ตัวอักษรของแม่แบบ)")
    if not body.strip():
        errors.append("กรุณากรอกเนื้อหา")
    if len(body) > 3000:
        errors.append("เนื้อหายาวเกินไป (เกิน 3,000 ตัวอักษรของแม่แบบ)")
    for label, text in (("ชื่อเรื่อง", title), ("เนื้อหา", body)):
        unknown = sorted({n for n in PH_RE.findall(text)} - ok)
        if unknown:
            errors.append(f"{label}: ตัวแปรที่ใช้กับเหตุการณ์นี้ไม่ได้: " + ", ".join("{" + n + "}" for n in unknown))
        stripped = OPT_RE.sub("", text)
        if "[[" in stripped or "]]" in stripped:
            errors.append(f"{label}: วงเล็บส่วนเสริม [[ ]] ไม่ครบคู่")
        rest = PH_RE.sub("", text)
        if "{" in rest or "}" in rest:
            errors.append(f"{label}: มีวงเล็บปีกกา {{ }} ที่ไม่ใช่ตัวแปร (ใช้เฉพาะรูปแบบ {{code}})")
        if CTRL_RE.search(text):
            errors.append(f"{label}: มีอักขระควบคุมที่ไม่อนุญาต")
    if "\n" in title or "\r" in title:
        errors.append("ชื่อเรื่องต้องเป็นบรรทัดเดียว")
    return errors


def save(db, event: str, title: str, body: str, user_id: int | None = None) -> list[str]:
    title, body = title.strip(), body.replace("\r\n", "\n").strip()
    errors = validate(event, title, body)
    if errors:
        return errors
    if (title, body) == DEFAULTS[event]:
        db.execute("DELETE FROM notify_templates WHERE event=?", (event,))               # เหมือนมาตรฐานทุกตัวอักษร = ไม่ต้องเก็บ
    else:
        db.execute("INSERT INTO notify_templates(event, title, body, updated_by, updated_at) VALUES(?,?,?,?,datetime('now','localtime')) "
                   "ON CONFLICT(event) DO UPDATE SET title=excluded.title, body=excluded.body, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                   (event, title, body, user_id))
    db.commit()
    return []


def reset(db, event: str) -> None:
    db.execute("DELETE FROM notify_templates WHERE event=?", (event,))
    db.commit()


def sample_values(event: str) -> dict:
    base = {k: SAMPLES[k] for k in allowed(event)}
    base.update({k: v for k, v in EVENT_SAMPLES.get(event, {}).items() if k in base})
    if event.startswith("incident_") and "kind" in base:
        base["kind"] = "อุบัติเหตุ"
    return base
