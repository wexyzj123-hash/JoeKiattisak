import pytest
from PIL import Image

from shared import mascot


@pytest.fixture
def assets(tmp_path, monkeypatch):
    Image.new("RGBA", (40, 40), (0, 0, 255, 0)).save(tmp_path / "default.png")
    Image.new("RGBA", (40, 40), (0, 0, 255, 0)).save(tmp_path / "alert.webp")
    monkeypatch.setattr(mascot, "ASSET_DIR", tmp_path)
    return tmp_path


def test_without_image_files_the_vector_is_used(tmp_path):
    out = mascot.mascot_markup("default", 100, asset_dir=tmp_path)
    assert out.startswith("<svg") and "<img" not in out


def test_image_file_replaces_the_vector_for_that_mood_only(assets):
    img = mascot.mascot_markup("default", 96)
    assert img.startswith("<img") and 'src="/static/mascot/default.png?v=' in img and 'width="96"' in img
    assert 'alt="น้องเอเอียต"' in img and 'class="mascot mascot-default"' in img
    assert 'src="/static/mascot/alert.webp?v=' in mascot.mascot_markup("alert", 64)
    assert mascot.mascot_markup("earth", 64).startswith("<svg")                      # ท่าที่ยังไม่มีไฟล์ใช้เวกเตอร์
    assert mascot.mascot_markup("nope", 64).startswith("<img")                       # ท่าที่ไม่รู้จัก → default


def test_decorative_image_is_hidden_and_label_is_escaped(assets):
    deco = mascot.mascot_markup("default", 80, "x", decorative=True)
    assert 'alt=""' in deco and 'aria-hidden="true"' in deco
    evil = mascot.mascot_markup("default", 80, '"><script>alert(1)</script>')
    assert "<script>" not in evil and "&quot;&gt;&lt;script&gt;" in evil


def test_size_clamped_for_images(assets):
    assert 'width="512"' in mascot.mascot_markup("default", 9999) and 'width="16"' in mascot.mascot_markup("default", 1)


@pytest.mark.parametrize("which", ["hub", "safety"])
def test_static_route_serves_only_whitelisted_mascot_files(which, hub_app, safety_app, assets):
    c = (hub_app if which == "hub" else safety_app).test_client()
    ok = c.get("/static/mascot/default.png")
    assert ok.status_code == 200 and ok.mimetype == "image/png"
    assert c.get("/static/mascot/alert.webp").mimetype == "image/webp"
    for bad in ("earth.png", "Default.png", "default.svg", "..%2fapp.py", "default.png.exe", "%2e%2e/default.png"):
        assert c.get(f"/static/mascot/{bad}").status_code == 404, bad


@pytest.mark.parametrize("which", ["hub", "safety"])
def test_jinja_global_uses_images_when_present(which, hub_app, safety_app, assets):
    app = hub_app if which == "hub" else safety_app
    assert app.jinja_env.globals["mascot"]("default", 50).startswith("<img")


@pytest.mark.parametrize("which", ["hub", "safety"])
def test_brand_route_serves_only_the_two_logo_files(which, hub_app, safety_app):
    c = (hub_app if which == "hub" else safety_app).test_client()
    for name in ("aisin.png", "aisin-white.png"):
        r = c.get(f"/static/brand/{name}")
        assert r.status_code == 200 and r.mimetype == "image/png"
    assert c.get("/static/brand/other.png").status_code == 404 and c.get("/static/brand/..%2fmascot%2fdefault.png").status_code == 404
