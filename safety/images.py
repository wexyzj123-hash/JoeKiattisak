"""safety/images.py — รูปถ่ายประกอบรายการอันตราย: ตรวจจากการถอดรหัสจริง (ไม่เชื่อชื่อไฟล์/นามสกุล),
จำกัดขนาด/พิกเซล, หมุนตาม EXIF แล้วล้างเมทาดาทาทั้งหมด, บันทึกใหม่เป็น JPEG ชื่อที่ระบบสร้างเอง"""
from __future__ import annotations

import io
import os
import uuid
import warnings

from PIL import Image, ImageOps

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 1600
MAX_PER_KIND = 5
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}      # MPO = JPEG หลายภาพของกล้องโทรศัพท์ (ใช้ภาพหลักภาพเดียว)
KINDS = ("before", "after")


class ImageError(Exception):
    """ข้อความภาษาไทยสำหรับแสดงให้ผู้ใช้"""


def process_image(data: bytes) -> bytes:
    if not data:
        raise ImageError("ไฟล์ว่างเปล่า")
    if len(data) > MAX_BYTES:
        raise ImageError("ไฟล์ใหญ่เกิน 10 MB")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            img = Image.open(io.BytesIO(data))
        except Exception as exc:                                  # noqa: BLE001 — ไฟล์เสีย/ไม่ใช่รูป/ระเบิดพิกเซล
            raise ImageError("ไฟล์ไม่ใช่รูปภาพที่รองรับ (รองรับ JPEG, PNG, WebP)") from exc
        if img.format not in ALLOWED_FORMATS:
            raise ImageError("ไฟล์ไม่ใช่รูปภาพที่รองรับ (รองรับ JPEG, PNG, WebP)")
        if img.width * img.height > MAX_PIXELS:
            raise ImageError("ขนาดภาพ (จำนวนพิกเซล) ใหญ่เกินไป")
        if img.format != "MPO" and getattr(img, "is_animated", False):
            raise ImageError("ไม่รองรับภาพเคลื่อนไหว")
        if img.format == "MPO":
            img.seek(0)
        try:
            img.load()
        except Exception as exc:                                  # noqa: BLE001 — ไฟล์ขาด/เสีย
            raise ImageError("ไฟล์รูปภาพเสียหายหรือไม่สมบูรณ์") from exc
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P", "PA") and (img.mode != "P" or "transparency" in img.info):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.getchannel("A"))
            img = flat
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82, optimize=True)         # ไม่ส่ง exif → ไม่มีเมทาดาทาติดไปด้วย
    return out.getvalue()


def sanitize_name(filename: str) -> str:
    """ชื่อสำหรับแสดงผลเท่านั้น (ไม่เคยใช้เป็น path): เอาเฉพาะชื่อไฟล์ ตัดอักขระควบคุม ไม่เกิน 100 ตัว"""
    name = str(filename or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127).strip()
    if name in ("", ".", ".."):
        return "photo"
    return name[:100]


# ตาราง/คอลัมน์ที่อนุญาตเท่านั้น (ชื่อ scope ไม่เคยถูกต่อเข้า SQL ตรง ๆ)
SCOPES = {"hazard": ("hazard_attachments", "hazard_id", "", True), "incident": ("incident_attachments", "incident_id", "incident-", True),
          "patrol": ("patrol_attachments", "answer_id", "patrol-", False)}          # รูปของรอบตรวจผูกกับข้อคำตอบ ไม่มีประเภทก่อน/หลัง


def save_attachment(db, upload_root: str, owner_id: int, kind: str, filename: str, data: bytes, user_id: int,
                    scope: str = "hazard") -> int:
    if scope not in SCOPES:
        raise ValueError("unknown attachment scope")
    table, fk, prefix, has_kind = SCOPES[scope]
    if has_kind and kind not in KINDS:
        raise ImageError("ประเภทรูปภาพไม่ถูกต้อง")
    used = db.execute(f"SELECT COUNT(*) FROM {table} WHERE {fk}=?" + (" AND kind=?" if has_kind else ""),
                      (owner_id, kind) if has_kind else (owner_id,)).fetchone()[0]
    if used >= MAX_PER_KIND:
        raise ImageError(f"แนบรูปได้ไม่เกิน {MAX_PER_KIND} รูปต่อประเภท")
    jpeg = process_image(data)
    folder = os.path.join(upload_root, f"{prefix}{int(owner_id)}")
    os.makedirs(folder, exist_ok=True)
    stored = uuid.uuid4().hex + ".jpg"
    path = os.path.join(folder, stored)
    with open(path, "xb") as fh:
        fh.write(jpeg)
    try:
        if has_kind:
            cur = db.execute(f"INSERT INTO {table}({fk}, kind, stored_name, orig_name, size, uploaded_by) "
                             "VALUES(?,?,?,?,?,?)", (owner_id, kind, stored, sanitize_name(filename), len(jpeg), user_id))
        else:
            cur = db.execute(f"INSERT INTO {table}({fk}, stored_name, orig_name, size, uploaded_by) "
                             "VALUES(?,?,?,?,?)", (owner_id, stored, sanitize_name(filename), len(jpeg), user_id))
        db.commit()
    except Exception:
        db.rollback()
        os.remove(path)
        raise
    return cur.lastrowid
