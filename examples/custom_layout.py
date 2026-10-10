"""Draw your own label with Pillow and print it.

    python examples/custom_layout.py                        # only saves label.png
    python examples/custom_layout.py --host 192.168.1.50    # also prints it

The label is a 480 x 320 image (60 x 40 mm at 203 dpi). Anything dark (< 50 % grey) is printed.
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import niimbot_k3w as k  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--host", default=k.HOST, help="printer IP (default: NIIMBOT_HOST); omit to only save label.png")
ap.add_argument("--port", type=int, default=k.PORT)
args = ap.parse_args()

w, h = k.label_size()  # (480, 320)
font = k.find_font()
img = Image.new("L", (w, h), 255)  # white background
d = ImageDraw.Draw(img)
d.rectangle([4, 4, w - 5, h - 5], outline=0, width=4)
d.text((w // 2, 115), "A-1024", font=ImageFont.truetype(font, 100), fill=0, anchor="mm")
d.line([28, 195, w - 29, 195], fill=0, width=3)
d.text((w // 2, 252), "存放：仓库 B-03", font=ImageFont.truetype(font, 44), fill=0, anchor="mm")
img.save("label.png")
print("saved label.png")

if args.host:
    k.print_label(img, args.host, args.port)
    print("printed 1 label")
