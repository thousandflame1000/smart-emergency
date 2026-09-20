# -*- coding: utf-8 -*-
"""重畫 Rich Menu 圖片到 app/static/richmenu/（需要 Windows 字型，改版面時在本機跑一次再 commit）。"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from app.services.rich_menu import IMAGE_DIR, MENUS, H, W, layout  # noqa: E402

FONT = "C:/Windows/Fonts/msjhbd.ttc"
EMOJI = "C:/Windows/Fonts/seguiemj.ttf"


def draw_menu(rows, path):
    img = Image.new("RGB", (W, H), "#f0f4f8")
    d = ImageDraw.Draw(img)
    for x, y, w, h, (label, sub, bg, icon, _text) in layout(rows):
        d.rectangle([x + 8, y + 8, x + w - 8, y + h - 8], fill=bg)
        cx = x + w // 2
        label_size = min(int(h * 0.20), int(w * 0.86 / len(label)))
        f_label = ImageFont.truetype(FONT, label_size)
        f_sub = ImageFont.truetype(FONT, int(h * 0.11))
        f_icon = ImageFont.truetype(EMOJI, int(h * 0.24))
        d.text((cx, y + int(h * 0.10)), icon, font=f_icon, embedded_color=True, anchor="mt")
        d.text((cx, y + int(h * 0.47)), label, font=f_label, fill="#ffffff", anchor="mt")
        d.text((cx, y + int(h * 0.76)), sub, font=f_sub, fill="#f2f2f2", anchor="mt")
    img.save(path, "PNG", optimize=True)
    print("saved", path, os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    os.makedirs(IMAGE_DIR, exist_ok=True)
    for spec in MENUS.values():
        draw_menu(spec["rows"], os.path.join(IMAGE_DIR, spec["image"]))
