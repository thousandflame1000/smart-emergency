# -*- coding: utf-8 -*-
"""LINE 卡片的顏色也要看得清楚。

網頁那套量測跑不到 Flex 訊息，所以這些顏色一直沒被檢查過：任務卡的標頭、
接單鈕、距離文字全都低於門檻（2.75–3.54:1）。志工是在災害現場、可能戶外
強光下看這張卡，那是最不能將就的情境。

門檻依 WCAG 1.4.3：一般文字 4.5:1，大字粗體（卡片標頭、按鈕文字）3:1。
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "app"
FILES = ["services/line_notify.py", "services/line_ops.py", "routers/linebot.py"]

# 白字放在這些底色上（卡片標頭、主要按鈕）。
ON_COLOR_WHITE_TEXT = 3.0
# 這些是印在白色卡片內文上的字色。
ON_WHITE_BODY_TEXT = 4.5


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _colours() -> set[str]:
    found: set[str] = set()
    for name in FILES:
        found |= set(re.findall(r"#[0-9a-fA-F]{6}", (SRC / name).read_text(encoding="utf-8")))
    return {c for c in found if c.lower() != "#ffffff"}


def test_white_text_on_card_colours_is_legible():
    """標頭與主要按鈕是白字，底色不能太淺。"""
    too_light = {c: round(contrast("#ffffff", c), 2)
                 for c in _colours() if contrast("#ffffff", c) < ON_COLOR_WHITE_TEXT}
    # 純內文色（例如次要說明的灰）本來就不放白字，個別排除。
    body_only = {"#6d6d6d", "#555555", "#0d7f85", "#8a6000"}
    too_light = {c: r for c, r in too_light.items() if c not in body_only}
    assert not too_light, f"白字放在這些底色上看不清楚：{too_light}"


@pytest.mark.parametrize("colour", ["#6d6d6d", "#0d7f85", "#8a6000", "#555555"])
def test_body_text_colours_are_legible_on_a_white_card(colour):
    got = contrast(colour, "#ffffff")
    assert got >= ON_WHITE_BODY_TEXT, f"{colour} 在白色卡片上只有 {got:.2f}:1"


def test_no_known_failing_colour_comes_back():
    """這些是量測後換掉的值，別再被貼回來。"""
    retired = {"#FF6B35": 2.84, "#e67e22": 2.85, "#27ae60": 2.87,
               "#27ACB2": 2.75, "#888888": 3.54, "#E69F00": 2.25}
    present = {c: r for c, r in retired.items() if c in _colours()}
    assert not present, f"低對比的舊色又出現了：{present}"
