import pytest
from reportlab.pdfbase import pdfmetrics

from safety import report_fonts


def test_register_returns_names_and_is_idempotent():
    assert report_fonts.available()
    first = report_fonts.register()
    assert report_fonts.register() == first == ("ReportFont", "ReportFont-Bold")
    assert "ReportFont" in pdfmetrics.getRegisteredFontNames()


def test_font_covers_thai_glyphs():
    face = pdfmetrics.getFont(report_fonts.register()[0]).face
    for ch in "กำิ่๑ฯ":
        assert ord(ch) in face.charToGlyph, ch


def test_missing_font_raises_font_missing_with_thai_message(tmp_path, monkeypatch):
    monkeypatch.setattr(report_fonts, "FONT_DIR", str(tmp_path))
    assert not report_fonts.available()
    with pytest.raises(report_fonts.FontMissing) as exc:
        report_fonts.register()
    assert "ฟอนต์ไทย" in str(exc.value)
