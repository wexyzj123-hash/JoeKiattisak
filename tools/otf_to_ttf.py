"""แปลงฟอนต์ OpenType (CFF) เป็น TrueType เพื่อฝังใน PDF ด้วย reportlab (ใช้ครั้งเดียวตอนเตรียมฟอนต์; ต้องมี fonttools)
ใช้: python tools/otf_to_ttf.py Loma.otf shared/assets/fonts/Loma-Regular.ttf"""
import sys

from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable


def convert(src: str, dst: str) -> None:
    font = TTFont(src)
    order = font.getGlyphOrder()
    glyphs, gs = {}, font.getGlyphSet()
    for name in order:
        pen = TTGlyphPen(gs)
        gs[name].draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))
        glyphs[name] = pen.glyph()
    font["loca"] = newTable("loca")
    glyf = font["glyf"] = newTable("glyf")
    glyf.glyphOrder, glyf.glyphs = order, glyphs
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    for attr in ("maxZones", "maxTwilightPoints", "maxStorage", "maxFunctionDefs", "maxInstructionDefs", "maxStackElements",
                 "maxSizeOfInstructions", "maxComponentElements"):
        setattr(maxp, attr, 0)
    maxp.maxZones = 1
    maxp.maxComponentDepth = 0
    font["head"].glyphDataFormat = 0
    font["post"].formatType = 2.0
    font["post"].extraNames, font["post"].mapping, font["post"].glyphOrder = [], {}, order
    font.sfntVersion = "\x00\x01\x00\x00"
    font.save(dst)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
