"""safety/report_fonts.py — ฟอนต์ไทยสำหรับ PDF (ฝังในที่เก็บ ไม่ต้องดาวน์โหลดตอนทำงาน)
ลำดับ: Sarabun (OFL) ถ้ามี แล้วจึง Loma (GPL + font exception)"""
from __future__ import annotations

import os

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared", "assets", "fonts")
FAMILIES = ("Sarabun", "Loma")
REGULAR, BOLD = "ReportFont", "ReportFont-Bold"


class FontMissing(RuntimeError):
    pass


def _find() -> tuple[str, str] | None:
    for fam in FAMILIES:
        reg, bold = os.path.join(FONT_DIR, f"{fam}-Regular.ttf"), os.path.join(FONT_DIR, f"{fam}-Bold.ttf")
        if os.path.isfile(reg) and os.path.isfile(bold):
            return reg, bold
    return None


def available() -> bool:
    return _find() is not None


def register() -> tuple[str, str]:
    found = _find()
    if found is None:
        raise FontMissing("ไม่พบฟอนต์ไทยสำหรับ PDF (shared/assets/fonts) — ส่งออก Excel แทนได้")
    if REGULAR not in pdfmetrics.getRegisteredFontNames() or getattr(register, "_src", None) != found:
        pdfmetrics.registerFont(TTFont(REGULAR, found[0]))
        pdfmetrics.registerFont(TTFont(BOLD, found[1]))
        pdfmetrics.registerFontFamily(REGULAR, normal=REGULAR, bold=BOLD, italic=REGULAR, boldItalic=BOLD)
        register._src = found
    return REGULAR, BOLD
