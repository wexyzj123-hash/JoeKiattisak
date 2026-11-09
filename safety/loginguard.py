"""safety/loginguard.py — จำกัดการเดารหัสผ่าน: ผิดครบ MAX_TRIES ครั้งในช่วง WINDOW วินาที (ต่อ IP+ชื่อผู้ใช้) ล็อกชั่วคราว LOCK_SECONDS วินาที
เก็บในหน่วยความจำของโปรเซส (รีสตาร์ตแล้วเริ่มใหม่) — พอสำหรับเครือข่ายภายในบริษัท"""
from __future__ import annotations

import threading
import time

MAX_TRIES, WINDOW, LOCK_SECONDS = 5, 600.0, 300.0
IP_MAX_TRIES = 25                                  # เดาหลายชื่อผู้ใช้จาก IP เดียว


class LoginGuard:
    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    def _key(self, ip: str, username: str) -> tuple[str, str]:
        return f"{ip}|{(username or '').strip().lower()[:64]}", f"ip:{ip}"

    def locked_for(self, ip: str, username: str, now: float | None = None) -> int:
        """จำนวนวินาทีที่ยังถูกล็อก (0 = ล็อกอินได้)"""
        now = time.time() if now is None else now
        with self._lock:
            left = max((self._locked.get(k, 0.0) - now for k in self._key(ip, username)), default=0.0)
            return int(left) + 1 if left > 0 else 0

    def failed(self, ip: str, username: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for key, limit in zip(self._key(ip, username), (MAX_TRIES, IP_MAX_TRIES)):
                hits = [t for t in self._fails.get(key, []) if now - t < WINDOW] + [now]
                self._fails[key] = hits
                if len(hits) >= limit:
                    self._locked[key] = now + LOCK_SECONDS
                    self._fails[key] = []
            if len(self._fails) > 5000:                          # กันหน่วยความจำบวม
                self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < WINDOW}
                self._locked = {k: v for k, v in self._locked.items() if v > now}

    def ok(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop(self._key(ip, username)[0], None)
