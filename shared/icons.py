"""shared/icons.py — ชุดไอคอนเส้น 24×24 แบบ inline SVG (ไม่มีไฟล์ภายนอก ไม่มี id) สีตาม currentColor
ไอคอนตกแต่ง → aria-hidden; ส่ง label เพื่อให้เป็น role=img ที่อ่านออกเสียงได้ (ปุ่มที่มีแต่ไอคอนต้องมีชื่อเสมอ)"""
from __future__ import annotations

from markupsafe import Markup, escape

ICONS: dict[str, str] = {
    "home": '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "overview": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "departments": '<circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4M12 11.5H5v5M12 11.5h7v5"/>',
    "map-pin": '<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "clipboard": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4h6v3H9z"/><path d="M8.5 12h7M8.5 16h5"/>',
    "source": '<path d="M12 3v18M3 12h18"/><circle cx="12" cy="12" r="8"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
    "building": '<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M9 8h2M13 8h2M9 12h2M13 12h2M10 21v-4h4v4"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "users": '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/><circle cx="17" cy="9" r="2.5"/><path d="M17 14c2.5.2 4 2 4 5"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.3 8-8 9-4.7-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5L16 9.5"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
    "puzzle": '<path d="M8 4h4a2 2 0 1 1 4 0h3v5a2 2 0 1 0 0 4v6H5v-6a2 2 0 1 1 0-4V4z"/>',
    "bell": '<path d="M6 17V11a6 6 0 0 1 12 0v6l1.5 2h-15z"/><path d="M10 21h4"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/>',
    "sun-moon": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "logout": '<path d="M10 4H5v16h5"/><path d="M14 8l4 4-4 4M18 12H9"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5"/><circle cx="12" cy="18" r=".8"/>',
    "check": '<path d="M4 12.5l5 5L20 6.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 13h6M9 17h6"/>',
}
_FALLBACK = '<circle cx="12" cy="12" r="8"/>'


def _clamp(size) -> int:
    if isinstance(size, bool):
        return 20
    try:
        return max(12, min(64, int(size)))
    except (TypeError, ValueError):
        return 20


def icon(name: str, size: int = 20, label: str | None = None) -> Markup:
    body = ICONS.get(name, _FALLBACK) if isinstance(name, str) else _FALLBACK
    px = _clamp(size)
    a11y = f'role="img" aria-label="{escape(str(label))}"' if label else 'aria-hidden="true"'
    return Markup(f'<svg xmlns="http://www.w3.org/2000/svg" class="icon" viewBox="0 0 24 24" width="{px}" height="{px}" fill="none" '
                  f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false" {a11y}>{body}</svg>')
