"""Draws the app icon: assets/icon.ico (exe, installer) and ideas_gui/ui/icon.png (tray, favicon).

    python assets/make_icon.py
"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024
INK, BULB, BASE = (35, 32, 27, 255), (240, 182, 72, 255), (214, 205, 190, 255)


def draw():
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 40, S - 40, S - 40), radius=210, fill=INK)
    # Bulb: a circle flowing into a neck
    cx, cy, r = S // 2, 430, 235
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=BULB)
    d.polygon([(cx - 190, cy + 140), (cx + 190, cy + 140), (cx + 120, 700), (cx - 120, 700)], fill=BULB)
    # Screw base
    for i, y in enumerate((730, 800)):
        w = 120 - i * 14
        d.rounded_rectangle((cx - w, y, cx + w, y + 48), radius=24, fill=BASE)
    d.rounded_rectangle((cx - 60, 866, cx + 60, 900), radius=17, fill=BASE)
    # Highlight
    d.arc((cx - r + 70, cy - r + 70, cx + r - 70, cy + r - 70), 200, 255, fill=(255, 238, 200, 255), width=34)
    return img


def main():
    img = draw()
    png = os.path.join(ROOT, 'ideas_gui', 'ui', 'icon.png')
    img.resize((256, 256), Image.LANCZOS).save(png)
    ico = os.path.join(ROOT, 'assets', 'icon.ico')
    img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print('wrote', png, 'and', ico)


if __name__ == '__main__':
    main()
