"""Image conversion: what ends up as black dots for unusual input images."""
from PIL import Image, ImageDraw

import niimbot_k3w as k


def black_dots(img):
    bits = k.to_bits(img)
    return sum(bin(b).count("1") for b in bits.tobytes())


def test_16_bit_grayscale_is_scaled_like_8_bit():
    g8 = Image.new("L", (300, 200), 255)
    ImageDraw.Draw(g8).rectangle([50, 50, 249, 149], fill=60)
    g16 = g8.convert("I").point(lambda v: v * 257).convert("I;16")
    assert black_dots(g16) == black_dots(g8) == 200 * 100


def test_exif_orientation_is_applied():
    img = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(img).rectangle([0, 0, 149, 199], fill="black")  # left half black
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90° clockwise when displayed
    img = Image.open(_roundtrip_jpeg(img, exif))
    bits = k.to_bits(img)
    assert bits.size == (200, 300)  # upright: portrait
    top, bottom = bits.crop((0, 0, 200, 150)), bits.crop((0, 150, 200, 300))
    assert top.getbbox() is not None and bottom.getbbox() is None


def _roundtrip_jpeg(img, exif):
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes(), quality=95)
    buf.seek(0)
    return buf


def test_transparent_color_key_counts_as_white():
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    ImageDraw.Draw(img).rectangle([25, 25, 74, 74], fill=(10, 10, 10))
    img.info["transparency"] = (0, 0, 0)  # pure black is the transparent colour (PNG tRNS)
    assert black_dots(img) == 50 * 50  # only the near-black square prints; the transparent background is white


def test_rgba_transparency_counts_as_white():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([0, 0, 49, 99], fill=(0, 0, 0, 255))
    assert black_dots(img) == 50 * 100


def test_small_images_are_enlarged_without_blur():
    img = Image.new("L", (4, 4), 255)
    img.putpixel((0, 0), 0)
    img.putpixel((3, 3), 0)
    bits = k.prepare(img, (480, 320))  # 4x4 -> 320x320 centred, 80 dots per pixel
    assert sum(bin(b).count("1") for b in bits.tobytes()) == 2 * 80 * 80
    assert bits.getpixel((80, 0)) and bits.getpixel((399, 319))
