"""hub/org.py — ดึงรายชื่อโรงงาน/แผนก/ไลน์จาก Safety ให้ฟอร์มขอสร้างบัญชี (เรียกฝั่งเซิร์ฟเวอร์ด้วย service token)
ถ้าติดต่อ Safety ไม่ได้ ฟอร์มจะให้พิมพ์ข้อความเองแทน — การขอบัญชีต้องไม่ล้มเพราะระบบลูกหยุดอยู่"""
from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen

_cache: dict = {"at": 0.0, "key": None, "data": None}
TTL = 20.0


def fetch_org(safety_url: str | None, token: str, timeout: float = 2.0):
    if not safety_url or not token:
        return None
    key = (safety_url, token)
    if _cache["key"] == key and time.time() - _cache["at"] < TTL:
        return _cache["data"]
    data = None
    try:
        req = Request(safety_url.rstrip("/") + "/api/org", headers={"X-Service-Token": token})
        with urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read(1_000_000).decode("utf-8"))
        data = {k: [r for r in raw.get(k, []) if isinstance(r, dict) and isinstance(r.get("id"), int) and isinstance(r.get("name"), str)]
                for k in ("factories", "departments", "lines")}
        if not data["factories"]:
            data = None                                   # ยังไม่ได้กรอกข้อมูลหลักใน Safety → ใช้ช่องพิมพ์เอง
    except Exception:                                     # noqa: BLE001 — ของเสริม ห้ามทำให้หน้าขอบัญชีล้ม
        data = None
    _cache.update(at=time.time(), key=key, data=data)
    return data


def resolve(org: dict, factory_id, department_id, line_id):
    """คืน (โรงงาน, แผนก, ไลน์) เป็นชื่อ ถ้าเลือกสอดคล้องกันตามสาย ไม่งั้นคืน None"""
    def one(rows, ident):
        try:
            ident = int(ident)
        except (TypeError, ValueError):
            return None
        return next((r for r in rows if r["id"] == ident), None)
    f = one(org["factories"], factory_id)
    d = one(org["departments"], department_id)
    if f is None or d is None or d.get("factory_id") != f["id"]:
        return None
    if str(line_id or "").strip() == "":
        return f["name"], d["name"], ""
    ln = one(org["lines"], line_id)
    if ln is None or ln.get("department_id") != d["id"]:
        return None
    return f["name"], d["name"], ln["name"]
