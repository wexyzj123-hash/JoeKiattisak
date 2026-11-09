import io
import os
import re

import pytest
from PIL import Image

from safety.db import connect
from safety.images import ImageError, process_image, sanitize_name, save_attachment
from safety.schema import ensure_schema
from tests.safety.factories import add_chain, add_hazard, add_user


def img_bytes(fmt="PNG", size=(40, 30), mode="RGB", color=(200, 30, 30), **save_kw) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **save_kw)
    return buf.getvalue()


def out_img(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_allowed_formats_become_jpeg_within_1600px(fmt):
    out = process_image(img_bytes(fmt, size=(3000, 2000)))
    im = out_img(out)
    assert im.format == "JPEG" and max(im.size) == 1600 and im.size == (1600, 1067)


def test_small_image_not_upscaled():
    assert out_img(process_image(img_bytes("PNG", size=(40, 30)))).size == (40, 30)


@pytest.mark.parametrize("data", [
    b"hello world, not an image", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<?xml version='1.0'?><svg/>", b"GIF89a\x01\x00\x01\x00", b"%PDF-1.4 fake", b"PK\x03\x04zip",
])
def test_non_images_and_svg_rejected(data):
    with pytest.raises(ImageError):
        process_image(data)


def test_text_file_named_jpg_rejected_regardless_of_name(tmp_path):
    db = connect(str(tmp_path / "s.db")); ensure_schema(db)
    ch, u = add_chain(db), add_user(db)
    hid = add_hazard(db, ch["a"], u)
    with pytest.raises(ImageError):
        save_attachment(db, str(tmp_path / "up"), hid, "before", "evil.jpg", b"just text", u)
    assert db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 0 and not (tmp_path / "up").exists()


def test_gif_and_bmp_rejected_even_though_pillow_decodes_them():
    for fmt in ("GIF", "BMP"):
        with pytest.raises(ImageError):
            process_image(img_bytes(fmt))


def test_empty_and_truncated_rejected():
    with pytest.raises(ImageError):
        process_image(b"")
    good = img_bytes("PNG", size=(300, 300), color=(1, 2, 3))
    with pytest.raises(ImageError):
        process_image(good[: len(good) // 2])


def test_oversized_bytes_rejected_before_decoding():
    with pytest.raises(ImageError, match="10"):
        process_image(b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024))


def test_pixel_bomb_rejected():
    buf = io.BytesIO()
    Image.new("1", (10000, 10000), 0).save(buf, "PNG")                    # 100 ล้านพิกเซล บีบอัดเล็กมาก
    assert len(buf.getvalue()) < 1_000_000
    with pytest.raises(ImageError, match="ใหญ่"):
        process_image(buf.getvalue())


def test_animated_webp_rejected():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(ImageError):
        process_image(buf.getvalue())


def test_exif_orientation_applied_and_all_metadata_stripped():
    im = Image.new("RGB", (200, 100), (10, 200, 10))
    exif = Image.Exif()
    exif[0x0112] = 6                                                       # หมุน 90°
    exif[0x010F] = "SecretCam"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    out = out_img(process_image(buf.getvalue()))
    assert out.size == (100, 200)                                          # หมุนตามแล้ว
    assert len(out.getexif()) == 0 and "exif" not in out.info and b"SecretCam" not in process_image(buf.getvalue())


def test_alpha_flattened_onto_white():
    out = out_img(process_image(img_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0)))).convert("RGB")
    assert min(out.getpixel((5, 5))) >= 250


def test_palette_transparency_flattened():
    buf = io.BytesIO()
    Image.new("P", (20, 20), 0).save(buf, "PNG", transparency=0)
    assert min(out_img(process_image(buf.getvalue())).convert("RGB").getpixel((3, 3))) >= 250


@pytest.mark.parametrize("raw, clean", [
    ("../../x.jpg", "x.jpg"), ("a\\b\\c.png", "c.png"), ("/etc/passwd", "passwd"), ("C:\\Windows\\a.jpg", "a.jpg"),
    ("ok\x00name\n.jpg", "okname.jpg"), ("", "photo"), ("   ", "photo"), ("../", "photo"), ("ก.jpg", "ก.jpg"),
    ("x" * 300 + ".jpg", ("x" * 300 + ".jpg")[:100]),
])
def test_sanitize_name(raw, clean):
    assert sanitize_name(raw) == clean


@pytest.fixture
def world(tmp_path):
    db = connect(str(tmp_path / "s.db")); ensure_schema(db)
    ch, u = add_chain(db), add_user(db)
    return db, add_hazard(db, ch["a"], u), u, str(tmp_path / "uploads")


def test_save_attachment_stores_generated_name_only_under_hazard_folder(world):
    db, hid, uid, root = world
    aid = save_attachment(db, root, hid, "before", "../../x.jpg", img_bytes("PNG"), uid)
    row = db.execute("SELECT * FROM hazard_attachments WHERE id=?", (aid,)).fetchone()
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", row["stored_name"]) and row["orig_name"] == "x.jpg" and row["kind"] == "before"
    path = os.path.join(root, str(hid), row["stored_name"])
    assert os.path.isfile(path) and os.path.getsize(path) == row["size"]
    walked = [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]
    assert walked == [path]


def test_sixth_photo_rejected_per_kind_but_other_kind_allowed(world):
    db, hid, uid, root = world
    for i in range(5):
        save_attachment(db, root, hid, "before", f"{i}.png", img_bytes("PNG"), uid)
    with pytest.raises(ImageError, match="5"):
        save_attachment(db, root, hid, "before", "6.png", img_bytes("PNG"), uid)
    save_attachment(db, root, hid, "after", "a.png", img_bytes("PNG"), uid)
    assert db.execute("SELECT COUNT(*) FROM hazard_attachments").fetchone()[0] == 6
    assert len(os.listdir(os.path.join(root, str(hid)))) == 6


@pytest.mark.parametrize("kind", ["during", "", "BEFORE", "../x"])
def test_bad_kind_rejected(world, kind):
    db, hid, uid, root = world
    with pytest.raises(ImageError):
        save_attachment(db, root, hid, kind, "a.png", img_bytes("PNG"), uid)
    assert not os.path.exists(root)
