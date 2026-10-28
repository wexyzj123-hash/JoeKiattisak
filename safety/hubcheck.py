"""safety/hubcheck.py — ตรวจว่าผู้ใช้ยังใช้งานได้ที่ Hub หรือไม่ (ผู้ใช้ที่ Hub ปิดแล้วต้องหลุดจาก Safety ภายใน ~60 วินาที)
จำผลต่อผู้ใช้ 60 วินาที; Hub ติดต่อไม่ได้ → "เปิดไว้ก่อน" (fail open) เพื่อให้ Safety ใช้งานต่อได้ แต่จำผลล้มเหลวไว้สั้น ๆ (15 วินาที)
เพื่อไม่ให้ Hub ช้าหน่วงทุกคำขอ และ log เพียงครั้งเดียวต่อเหตุขัดข้อง"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("safety.hubcheck")
OK_TTL, FAIL_TTL, TIMEOUT = 60.0, 15.0, 2.0
_urlopen = urllib.request.urlopen
_now = time.time


class _State:
    def __init__(self):
        self.cache: dict[int, tuple[float, bool]] = {}
        self.outage = False
        self.lock = threading.Lock()


def _fetch(app, hub_uid: int) -> tuple[bool, bool]:
    """(active, reachable)"""
    req = urllib.request.Request(f"{app.config['HUB_URL'].rstrip('/')}/api/user-status/{int(hub_uid)}",
                                 headers={"X-Service-Token": app.config["SERVICE_TOKEN"]})
    try:
        with _urlopen(req, timeout=TIMEOUT) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("active")), True
    except urllib.error.HTTPError as exc:
        return (False, True) if exc.code == 404 else (True, False)      # ไม่พบผู้ใช้ = ปิด; 401/5xx = ถือว่าตรวจไม่ได้
    except Exception:                                                    # noqa: BLE001 — เครือข่าย/หมดเวลา/ข้อมูลผิดรูปแบบ
        return True, False


def hub_user_active(app, hub_uid: int, now: float | None = None) -> bool:
    now = _now() if now is None else now
    state = app.extensions.setdefault("hubcheck", _State())
    with state.lock:
        hit = state.cache.get(hub_uid)
        if hit and now < hit[0]:
            return hit[1]
    active, reachable = _fetch(app, hub_uid)
    with state.lock:
        if reachable:
            state.outage = False
        elif not state.outage:
            state.outage = True
            log.warning("Hub unreachable — failing open for user status checks")
        state.cache[hub_uid] = (now + (OK_TTL if reachable else FAIL_TTL), active)
    return active
