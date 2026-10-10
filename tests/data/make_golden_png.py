"""Generate tests/data/golden.png: a deterministic 480x320 1-bit test image (no fonts, no anti-aliasing).

It exercises every row encoding case: blank rows (0x84), runs of identical rows, the forced
packet break at row 200, rows wider than the 3-chunk pixel count split, sparse rows with
only a few black dots, and completely black rows.
"""
import os

from PIL import Image, ImageDraw

W, H = 480, 320
img = Image.new("1", (W, H), 1)  # 1 = white
d = ImageDraw.Draw(img)
d.rectangle([0, 0, W - 1, H - 1], outline=0, width=3)           # border
d.rectangle([20, 20, 140, 120], fill=0)                          # solid block
d.rectangle([20, 190, 460, 215], fill=0)                         # identical rows across row 200
for x in range(160, 460, 12):                                    # vertical stripes
    d.line([x, 20, x, 120], fill=0, width=4)
d.line([20, 140, 460, 180], fill=0, width=2)                     # diagonal: every row differs
d.ellipse([300, 230, 400, 300], outline=0, width=5)
for i, x in enumerate((30, 90, 150, 210, 270)):                  # sparse rows: a few dots each
    d.point([(x, 250 + i), (x + 3, 250 + i)], fill=0)
d.rectangle([3, 300, W - 4, 304], fill=0)                        # full-width black rows

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.png")
img.save(out)
print(out)
