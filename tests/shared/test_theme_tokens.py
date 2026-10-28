import os
import re

import pytest

CSS = open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "theme.css"), encoding="utf-8").read()


def lum(hex_):
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block: str) -> dict:
    return dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;", block))


def block_after(selector: str) -> str:
    start = CSS.index(selector)
    return CSS[CSS.index("{", start) + 1: CSS.index("}", start)]


LIGHT = tokens(block_after(":root {"))                                                  # ธีมสว่างเป็นค่าเริ่มต้น
DARK = tokens(block_after(':root[data-theme="dark"]'))
DARK_MEDIA = tokens(block_after(':root:not([data-theme="light"])'))

TEXT_ON_SURFACE = [("text", s) for s in ("bg", "panel", "panel-2")] + [("muted", s) for s in ("bg", "panel", "panel-2")] + \
                  [("link", s) for s in ("bg", "panel", "panel-2")] + [("danger-text", s) for s in ("bg", "panel")] + \
                  [("ok-text", s) for s in ("bg", "panel")] + [("warn-text", s) for s in ("bg", "panel")] + \
                  [("text", "flash-bg"), ("text", "success-bg"), ("accent-ink", "accent"), ("danger-ink", "danger"),
                   ("primary-ink", "primary"), ("primary-ink", "primary-2"), ("shell-text", "shell-1"), ("shell-text", "shell-2")] + \
                  []


@pytest.mark.parametrize("name, theme", [("dark", DARK), ("light", LIGHT)])
@pytest.mark.parametrize("fg, bg", TEXT_ON_SURFACE)
def test_text_contrast_at_least_4_5_to_1(name, theme, fg, bg):
    assert fg in theme and bg in theme, (name, fg, bg)
    assert contrast(theme[fg], theme[bg]) >= 4.5, f"{name}: {fg} {theme[fg]} on {bg} {theme[bg]} = {contrast(theme[fg], theme[bg]):.2f}"


def test_dark_theme_defined_identically_for_manual_toggle_and_system_preference():
    assert DARK == DARK_MEDIA and len(DARK) >= 20


def test_light_is_the_default_and_dark_needs_toggle_or_system_preference():
    assert CSS.index(":root {") < CSS.index(':root[data-theme="dark"]')
    assert "prefers-color-scheme: dark" in CSS and "prefers-color-scheme: light" not in CSS
    assert re.search(r'html \{[^}]*color-scheme: light', CSS)


def test_brand_tokens_present():
    for t in ("primary", "primary-ink", "primary-2", "shell-1", "shell-2", "shell-text"):
        assert t in LIGHT and t in DARK


def test_theme_js_guards_storage_and_head_script_precedes_body():
    js = open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "theme.js"), encoding="utf-8").read()
    assert js.count("try {") >= 2 and "catch" in js
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    for tpl in ("safety/templates/base.html", "hub/templates/base.html"):
        html = open(os.path.join(root, tpl), encoding="utf-8").read()
        assert html.index("/static/theme.js") < html.index("</head>"), tpl


def test_both_themes_define_the_same_token_names():
    assert set(DARK) == set(LIGHT)


def test_level_chip_text_readable_on_default_level_colors():
    for color in ("#2ecc71", "#f1c40f", "#ff9f1c", "#ff4d4f"):
        assert contrast("#111111", color) >= 4.5, color


def test_no_hard_coded_surface_colors_that_break_light_theme():
    # โครงหน้าต้องใช้ token (เดิม sidebar/flash ใส่สีเข้มตายตัว ทำให้ธีมสว่างอ่านไม่ออก)
    for selector in (".sidebar", ".flash"):
        rule = CSS[CSS.index(selector + " {"): CSS.index("}", CSS.index(selector + " {"))]
        assert not re.search(r"background:\s*#", rule), (selector, rule)


def test_reduced_motion_disables_mascot_animation_and_motion_is_opt_in():
    assert "prefers-reduced-motion: no-preference" in CSS
    anim = CSS[CSS.index("prefers-reduced-motion: no-preference"):]
    assert "animation" in anim and "@keyframes" in CSS
    outside = CSS[:CSS.index("prefers-reduced-motion: no-preference")]
    assert "animation:" not in outside.replace("@keyframes", "")          # ไม่มีแอนิเมชันนอกบล็อกที่ผู้ใช้อนุญาต


def test_focus_visible_outline_defined():
    assert ":focus-visible" in CSS
