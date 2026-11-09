"""safety/search.py — ค้นหารายการอันตราย (รหัส/หัวข้อ) ตามสิทธิ์การมองเห็นเดียวกับหน้ารายการ"""
from __future__ import annotations

from .hazards import can_view_all, hazard_scope_sql
from .perms import user_permissions

MAX_QUERY = 100
LIMIT = 50


def clean_query(q) -> str:
    return (q if isinstance(q, str) else "").strip()[:MAX_QUERY].strip()


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_hazards(db, user, q, limit: int = LIMIT) -> list:
    q = clean_query(q)
    if not q:
        return []
    scope_sql, args = hazard_scope_sql(user)
    conds = [scope_sql, "(h.code LIKE ? ESCAPE '\\' OR h.title LIKE ? ESCAPE '\\')"]
    args = list(args) + [_like(q)] * 2
    if not can_view_all(user_permissions(db, user)):
        conds.append("(h.reported_by = ? OR h.owner_id = ?)")
        args += [user["id"]] * 2
    return db.execute(
        "SELECT h.id, h.code, h.title, h.status, h.score, h.level_name, h.level_color, h.reported_at, a.name_th AS area_name "
        "FROM hazards h JOIN risk_areas a ON a.id = h.risk_area_id WHERE " + " AND ".join(conds)
        + " ORDER BY h.reported_at DESC, h.id DESC LIMIT ?", args + [max(1, min(int(limit), LIMIT))]).fetchall()
