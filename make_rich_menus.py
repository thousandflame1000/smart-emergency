# -*- coding: utf-8 -*-
"""重畫 Rich Menu 圖片到 app/static/richmenu/（需要 Windows 字型，改版面時在本機跑一次再 commit）。"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from app.services.rich_menu import IMAGE_DIR, MENUS, H, W, layout  # noqa: E402

FONT = "C:/Windows/Fonts/msjhbd.ttc"
EMOJI = "C:/Windows/Fonts/seguiemj.ttf"


def _font_to_fit(draw, text, max_width, max_size, min_size):
    for size in range(max_size, min_size - 1, -2):
        font = ImageFont.truetype(FONT, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return ImageFont.truetype(FONT, min_size)


def _wrap_cjk(draw, text, font, max_width):
    """Wrap CJK copy character by character so no subtitle crosses its menu cell."""
    lines, current = [], ""
    for char in text:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def draw_menu(rows, path):
    img = Image.new("RGB", (W, H), "#f0f4f8")
    d = ImageDraw.Draw(img)
    for x, y, w, h, (label, sub, bg, icon, _text) in layout(rows):
        d.rectangle([x + 8, y + 8, x + w - 8, y + h - 8], fill=bg)
        cx = x + w // 2
        f_label = _font_to_fit(d, label, int(w * 0.82), int(h * 0.20), 38)
        f_sub = ImageFont.truetype(FONT, int(h * 0.10))
        f_icon = ImageFont.truetype(EMOJI, int(h * 0.24))
        wrapped_sub = _wrap_cjk(d, sub, f_sub, int(w * 0.84))
        sub_lines = wrapped_sub.splitlines()
        line_height = d.textbbox((0, 0), "測", font=f_sub)[3]
        sub_spacing = int(f_sub.size * 0.08)
        d.text((cx, y + int(h * 0.10)), icon, font=f_icon, embedded_color=True, anchor="mt")
        d.text((cx, y + int(h * 0.47)), label, font=f_label, fill="#ffffff", anchor="mt")
        for line_no, line in enumerate(sub_lines):
            d.text((cx, y + int(h * 0.70) + line_no * (line_height + sub_spacing)),
                   line, font=f_sub, fill="#f2f2f2", anchor="mt")
    img.save(path, "PNG", optimize=True)
    print("saved", path, os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    os.makedirs(IMAGE_DIR, exist_ok=True)
    for spec in MENUS.values():
        draw_menu(spec["rows"], os.path.join(IMAGE_DIR, spec["image"]))
