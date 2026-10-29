import re
import xml.etree.ElementTree as ET

import pytest
from markupsafe import Markup

from shared.icons import ICONS, icon

SVG = "{http://www.w3.org/2000/svg}"
REQUIRED = {"home", "overview", "departments", "map-pin", "clipboard", "source", "tag", "building", "layers", "users", "shield",
            "sliders", "puzzle", "bell", "user", "search", "sun-moon", "menu", "logout", "chevron-down", "plus", "alert", "check",
            "clock", "file"}


def test_required_icons_exist():
    assert REQUIRED <= set(ICONS)


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_every_icon_is_valid_svg_with_currentColor_and_no_ids(name):
    svg = str(icon(name))
    root = ET.fromstring(svg)
    assert root.tag == SVG + "svg" and root.get("viewBox") == "0 0 24 24"
    assert 'stroke="currentColor"' in svg and " id=" not in svg
    for bad in ("<script", "href", "http:", "url(", "<style"):
        assert bad not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert {e.tag.replace(SVG, "") for e in root.iter()} <= {"svg", "path", "circle", "rect", "title"}


def test_label_makes_it_accessible_and_default_hides_it():
    labelled = ET.fromstring(str(icon("bell", 20, 'แจ้ง"เตือน<')))
    assert labelled.get("role") == "img" and labelled.get("aria-label") == 'แจ้ง"เตือน<' and labelled.get("aria-hidden") is None
    hidden = ET.fromstring(str(icon("bell")))
    assert hidden.get("aria-hidden") == "true" and hidden.get("role") is None and hidden.get("aria-label") is None


def test_unknown_icon_falls_back_and_size_clamped_and_returns_markup():
    assert isinstance(icon("bell"), Markup)
    fallback = str(icon("nope-xyz"))
    ET.fromstring(fallback)
    assert "<circle" in fallback
    for bad, expected in ((5, 12), (500, 64), ("x", 20), (None, 20), (True, 20), (24, 24)):
        assert ET.fromstring(str(icon("bell", bad))).get("width") == str(expected)


@pytest.mark.parametrize("which", ["hub", "safety"])
def test_jinja_global_in_both_apps(which, hub_app, safety_app):
    app = hub_app if which == "hub" else safety_app
    out = app.jinja_env.globals["icon"]("search", 18)
    assert isinstance(out, Markup) and out.startswith("<svg")
