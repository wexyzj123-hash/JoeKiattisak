import re
import xml.etree.ElementTree as ET

import pytest
from markupsafe import Markup

from shared.mascot import DEFAULT_LABEL, MOODS, mascot_svg

SVG = "{http://www.w3.org/2000/svg}"


def parse(svg):
    return ET.fromstring(svg)


@pytest.mark.parametrize("mood", MOODS)
def test_every_mood_is_well_formed_xml(mood):
    root = parse(mascot_svg(mood, 100))
    assert root.tag == SVG + "svg" and len(list(root.iter())) > 10


def test_moods_are_the_expected_seven():
    assert MOODS == ("default", "confident", "thinking", "celebrate", "earth", "alert", "sleeping")


@pytest.mark.parametrize("mood", MOODS)
def test_has_role_img_title_viewbox_and_class(mood):
    root = parse(mascot_svg(mood, 96))
    assert root.get("role") == "img" and root.get("viewBox") == "0 0 200 240" and root.get("focusable") == "false"
    assert root.get("width") == "96" and root.get("height") == "96"
    assert root.get("class") == f"mascot mascot-{mood}"
    assert root.find(SVG + "title").text == DEFAULT_LABEL == "น้องเอเอียต"


@pytest.mark.parametrize("mood", MOODS)
def test_no_script_no_external_reference_no_event_handlers(mood):
    svg = mascot_svg(mood, 100)
    body = svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    for forbidden in ("<script", "<style", "<image", "<foreignObject", "<use", "<a ", "href", "xlink", "url(", "http", "javascript:", "data:"):
        assert forbidden not in body, forbidden
    assert not re.search(r"\son\w+\s*=", body)
    tags = {el.tag.replace(SVG, "") for el in parse(svg).iter()}
    assert tags <= {"svg", "title", "g", "path", "rect", "circle", "ellipse"}, tags


def test_unknown_mood_falls_back_to_default():
    default = mascot_svg("default", 120)
    for bad in ("nope", "", None, 5, "ALERT", "alert; <script>"):
        assert mascot_svg(bad, 120) == default


@pytest.mark.parametrize("size, expected", [(5, 16), (0, 16), (-3, 16), (16, 16), (64, 64), ("64", 64), (64.7, 64), (512, 512), (1000, 512),
                                            ("abc", 120), (None, 120), (True, 120), ([], 120), ("1e3", 120)])
def test_size_clamped_and_bad_size_defaults(size, expected):
    root = parse(mascot_svg("default", size))
    assert root.get("width") == root.get("height") == str(expected)


def test_label_is_escaped_and_xml_stays_valid():
    svg = mascot_svg("default", 80, '<b>"&\'</b>')
    root = parse(svg)                                                                       # ถ้า escape ไม่ถูกจะ parse ไม่ผ่าน
    assert root.find(SVG + "title").text == '<b>"&\'</b>'
    assert "<b>" not in svg and "&lt;b&gt;" in svg
    assert parse(mascot_svg("alert", 80, None)).find(SVG + "title").text == DEFAULT_LABEL
    assert parse(mascot_svg("alert", 80, 42)).find(SVG + "title").text == "42"


def test_moods_differ():
    assert len({mascot_svg(m, 100) for m in MOODS}) == len(MOODS)


@pytest.mark.parametrize("mood", MOODS)
def test_no_ids_so_many_on_one_page_do_not_collide(mood):
    svg = mascot_svg(mood, 100)
    assert " id=" not in svg
    page = f"<div>{svg}{svg}{svg}</div>"
    ids = [el.get("id") for el in ET.fromstring(page).iter() if el.get("id")]
    assert ids == []


def test_all_coordinates_inside_a_sane_range():
    for mood in MOODS:
        nums = [float(n) for n in re.findall(r'(?<![\w#.])-?\d+(?:\.\d+)?', re.sub(r'xmlns="[^"]*"|viewBox="[^"]*"|width="\d+"|height="\d+"|<title>.*?</title>', "", mascot_svg(mood, 100)))]
        assert nums and max(nums) < 400 and min(nums) > -50


@pytest.mark.parametrize("which", ["hub", "safety"])
def test_jinja_global_returns_markup_not_escaped_in_both_apps(which, hub_app, safety_app):
    app = hub_app if which == "hub" else safety_app
    out = app.jinja_env.globals["mascot"]("alert", 48, "สวัสดี")
    assert isinstance(out, Markup) and out.startswith("<svg")
    assert 'aria-hidden="true"' in app.jinja_env.globals["mascot"]("alert", 48, "x", True)
    from flask import render_template_string
    with app.test_request_context("/"):
        html = render_template_string("<p>{{ mascot('celebrate', 64) }}</p>")
    assert "<svg" in html and "&lt;svg" not in html and "mascot-celebrate" in html
    with app.test_request_context("/"):
        evil = render_template_string("{{ mascot('default', 64, label) }}", label="<script>alert(1)</script>")
    assert "<script>" not in evil and "&lt;script&gt;" in evil


@pytest.mark.parametrize("mood", MOODS)
def test_decorative_has_no_accessible_name(mood):
    svg = mascot_svg(mood, 80, "x", decorative=True)
    root = parse(svg)
    assert root.get("aria-hidden") == "true" and root.get("role") is None and root.find(SVG + "title") is None


@pytest.mark.parametrize("mood", MOODS)
def test_cap_and_chest_carry_the_aisin_lettering(mood):
    assert mascot_svg(mood, 100).count('class="m-logo"') == 2


def test_wordmark_scales_and_uses_given_color():
    from shared.brand import aisin_wordmark
    root = parse(aisin_wordmark(200, "#0b3b99"))
    assert root.get("role") == "img" and root.find(SVG + "title").text == "AISIN" and root.get("width") == "200"
    assert 'stroke="#0b3b99"' in aisin_wordmark(200, "#0b3b99")
    assert 'stroke="currentColor"' in aisin_wordmark(200, '"><script>')
    assert parse(aisin_wordmark("bad")).get("width") == "120"
