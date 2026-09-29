# -*- coding: utf-8 -*-
"""重畫 Rich Menu 圖片到 app/static/richmenu/（需要 Windows 字型，改版面時在本機跑一次再 commit）。

風格：淺底、白色圓角卡片、彩色圓形徽章裡放向量線條圖示，像手機 App 主畫面；
緊急按鈕整張紅色。先以兩倍解析度畫再縮小，邊緣才平滑。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
from app.services.rich_menu import IMAGE_DIR, MENUS, H, W, layout  # noqa: E402

BOLD = "C:/Windows/Fonts/msjhbd.ttc"
REGULAR = "C:/Windows/Fonts/msjh.ttc"
S = 2                      # 超取樣倍率
BG = "#eef3f0"
INK, MUTED, LINE = "#17201c", "#5f6c65", "#d9e2dc"
GAP, RADIUS = 28, 44       # 卡片間距與圓角（1 倍座標）


def font(path, size):
    return ImageFont.truetype(path, int(size * S))


def fit(draw, text, path, max_width, size, min_size=30):
    while size > min_size:
        f = font(path, size)
        if draw.textbbox((0, 0), text, font=f)[2] <= max_width * S:
            return f
        size -= 2
    return font(path, min_size)


# ── 向量圖示：在以 (cx, cy) 為中心、邊長 r*2 的方框內，用 color 畫粗線條 ──
def icon(d, name, cx, cy, r, color):
    w = max(6, int(r * 0.16))
    def line(pts):
        d.line(pts, fill=color, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)
    if name == "check":
        line([(cx - r * .55, cy + r * .02), (cx - r * .15, cy + r * .42), (cx + r * .6, cy - r * .4)])
    elif name == "sos":
        f = ImageFont.truetype(BOLD, int(r * .78))
        d.text((cx, cy + r * .02), "SOS", font=f, fill=color, anchor="mm")
    elif name == "compass":
        d.ellipse([cx - r * .72, cy - r * .72, cx + r * .72, cy + r * .72], outline=color, width=w)
        d.polygon([(cx, cy - r * .5), (cx + r * .2, cy), (cx, cy + r * .5), (cx - r * .2, cy)], fill=color)
    elif name == "chart":
        for i, hgt in enumerate((.45, .8, .6)):
            x = cx - r * .55 + i * r * .55
            d.rounded_rectangle([x - r * .17, cy + r * .6 - r * hgt * 1.2, x + r * .17, cy + r * .6], radius=w // 2, fill=color)
    elif name == "box":
        d.rounded_rectangle([cx - r * .6, cy - r * .45, cx + r * .6, cy + r * .6], radius=w, outline=color, width=w)
        line([(cx - r * .6, cy - r * .1), (cx + r * .6, cy - r * .1)])
        line([(cx - r * .15, cy + r * .15), (cx + r * .15, cy + r * .15)])
    elif name == "userplus":
        d.ellipse([cx - r * .55, cy - r * .62, cx - r * .05, cy - r * .12], outline=color, width=w)
        d.arc([cx - r * .85, cy + r * .02, cx + r * .25, cy + r * 1.05], 180, 360, fill=color, width=w)
        line([(cx + r * .5, cy - r * .35), (cx + r * .5, cy + r * .15)])
        line([(cx + r * .25, cy - r * .1), (cx + r * .75, cy - r * .1)])
    elif name == "monitor":
        d.rounded_rectangle([cx - r * .7, cy - r * .5, cx + r * .7, cy + r * .3], radius=w, outline=color, width=w)
        line([(cx, cy + r * .3), (cx, cy + r * .58)])
        line([(cx - r * .35, cy + r * .62), (cx + r * .35, cy + r * .62)])
    elif name == "form":
        d.rounded_rectangle([cx - r * .5, cy - r * .7, cx + r * .5, cy + r * .7], radius=w, outline=color, width=w)
        for k in (-.3, 0, .3):
            line([(cx - r * .25, cy + r * k), (cx + r * .25, cy + r * k)])
    elif name == "list":
        for k in (-.45, 0, .45):
            d.ellipse([cx - r * .62 - w * .7, cy + r * k - w * .7, cx - r * .62 + w * .7, cy + r * k + w * .7], fill=color)
            line([(cx - r * .3, cy + r * k), (cx + r * .62, cy + r * k)])
    elif name == "chat":
        d.rounded_rectangle([cx - r * .7, cy - r * .55, cx + r * .7, cy + r * .35], radius=r * .25, outline=color, width=w)
        d.polygon([(cx - r * .35, cy + r * .3), (cx - r * .5, cy + r * .72), (cx - r * .05, cy + r * .3)], fill=color)
        d.text((cx, cy - r * .1), "?", font=ImageFont.truetype(BOLD, int(r * .7)), fill=color, anchor="mm")
    elif name == "family":
        # 兩個實心人形（後面的小一點），一看就是「家人」
        for hx, hy, hr, body in ((cx + r * .36, cy - r * .3, r * .17, (cx + r * .04, cy + r * .02, cx + r * .7, cy + r * .72)),
                                 (cx - r * .2, cy - r * .36, r * .22, (cx - r * .64, cy - r * .02, cx + r * .24, cy + r * .82))):
            d.pieslice(body, 180, 360, fill=color)
            d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=color)
    elif name == "user":
        d.ellipse([cx - r * .28, cy - r * .62, cx + r * .28, cy - r * .06], outline=color, width=w)
        d.arc([cx - r * .6, cy + r * .02, cx + r * .6, cy + r * 1.1], 180, 360, fill=color, width=w)
    elif name == "grid":
        for gx in (-1, 1):
            for gy in (-1, 1):
                x, y = cx + gx * r * .32, cy + gy * r * .32
                d.rounded_rectangle([x - r * .24, y - r * .24, x + r * .24, y + r * .24], radius=w // 2, outline=color, width=w)
    elif name == "app":
        # 跟網站圖示同一個符號：房子裡一顆心
        d.polygon([(cx, cy - r * .7), (cx - r * .72, cy - r * .05), (cx - r * .5, cy - r * .05), (cx - r * .5, cy + r * .65),
                   (cx + r * .5, cy + r * .65), (cx + r * .5, cy - r * .05), (cx + r * .72, cy - r * .05)], fill=color)
        hr = r * .2
        hx, hy = cx, cy + r * .2
        d.ellipse([hx - hr * 1.9, hy - hr * 1.3, hx, hy + hr * .6], fill="#e0525f")
        d.ellipse([hx, hy - hr * 1.3, hx + hr * 1.9, hy + hr * .6], fill="#e0525f")
        d.polygon([(hx - hr * 1.85, hy - hr * .15), (hx + hr * 1.85, hy - hr * .15), (hx, hy + hr * 1.9)], fill="#e0525f")


def gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (w, h), top)
    t, b = Image.new("RGB", (1, 1), top).getpixel((0, 0)), Image.new("RGB", (1, 1), bottom).getpixel((0, 0))
    d = ImageDraw.Draw(img)
    for y in range(h):
        k = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * k) for i in range(3)))
    return img


def banner(img, d, box, cell):
    label, sub, color, name, _ = cell
    x0, y0, x1, y1 = box
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=RADIUS * S, fill=255)
    img.paste(gradient((x1 - x0, y1 - y0), "#16865f", "#0d5a45"), (x0, y0), mask.crop(box))
    h = y1 - y0
    r = h * .3
    cx, cy = x0 + h * .55, (y0 + y1) / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#ffffff")
    icon(d, name, cx, cy, r * .78, color)
    tx = cx + r + h * .22
    d.text((tx, cy - h * .06), "鄰里守望", font=font(BOLD, 118), fill="#ffffff", anchor="ls")
    d.text((tx, cy + h * .16), sub, font=fit(d, sub, REGULAR, (x1 - tx) / S - 520, 58), fill="#d7efe5", anchor="ls")
    pw, ph = 380 * S, 150 * S
    px1, py0 = x1 - 60 * S, cy - ph / 2
    d.rounded_rectangle([px1 - pw, py0, px1, py0 + ph], radius=ph / 2, fill="#ffffff")
    d.text((px1 - pw / 2, cy), "打開 ›", font=font(BOLD, 64), fill=color, anchor="mm")


def tab(d, box, cell, active):
    label, _, color, _, _ = cell
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    d.text((cx, cy - 6 * S), label, font=font(BOLD if active else REGULAR, 76),
           fill=color if active else MUTED, anchor="mm")
    if active:
        d.rounded_rectangle([cx - 110 * S, y1 - 22 * S, cx + 110 * S, y1 - 8 * S], radius=7 * S, fill=color)


def card(d, box, cell):
    label, sub, color, name, _ = cell
    x0, y0, x1, y1 = box
    danger = name == "sos"
    d.rounded_rectangle(box, radius=RADIUS * S, fill=color if danger else "#ffffff", outline=None if danger else LINE,
                        width=3 * S)
    w, h = x1 - x0, y1 - y0
    r = min(w, h) * .2
    cx, cy = (x0 + x1) / 2, y0 + h * .34
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#ffffff" if danger else color)
    icon(d, name, cx, cy, r * .82, color if danger else "#ffffff")
    d.text((cx, y0 + h * .69), label, font=fit(d, label, BOLD, w / S * .86, 100), fill="#ffffff" if danger else INK, anchor="mm")
    d.text((cx, y0 + h * .84), sub, font=fit(d, sub, REGULAR, w / S * .86, 52), fill="#fde2e6" if danger else MUTED,
           anchor="mm")


def draw_menu(spec, path):
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    cells = layout(spec["rows"], spec.get("heights"))
    nav_bottom = max(y + h for x, y, w, h, c in cells if c[3] == "tab")
    # 分頁列：白底、細底線，像 App 上方的 nav bar
    d.rectangle([0, 0, W * S, nav_bottom * S], fill="#ffffff")
    d.line([(0, nav_bottom * S - S), (W * S, nav_bottom * S - S)], fill=LINE, width=3 * S)
    for x, y, w, h, cell in cells:
        if cell[3] == "tab":
            tab(d, [x * S, y * S, (x + w) * S, (y + h) * S], cell, cell[4] == f"tab:{spec['alias']}")
            continue
        box = [int((x + GAP / 2) * S), int((y + GAP / 2) * S), int((x + w - GAP / 2) * S), int((y + h - GAP / 2) * S)]
        if cell[3] == "app":
            banner(img, d, box, cell)
        else:
            card(d, box, cell)
    img = img.resize((W, H), Image.LANCZOS)
    img.save(path, "PNG", optimize=True)
    print("saved", path, os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    os.makedirs(IMAGE_DIR, exist_ok=True)
    for spec in MENUS.values():
        draw_menu(spec, os.path.join(IMAGE_DIR, spec["image"]))
