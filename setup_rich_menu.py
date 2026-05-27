# -*- coding: utf-8 -*-
"""
LINE Bot Rich Menu 設定腳本
執行一次：python setup_rich_menu.py
會建立固定底部選單讓長者直接點按
"""
import sys, os, json, io
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image, ImageDraw, ImageFont

# ── 設定 ──────────────────────────────────────
from app.config import settings
ACCESS_TOKEN = settings.LINE_CHANNEL_ACCESS_TOKEN

# ── 1. 畫 Rich Menu 圖片（2500×1686）──────────

W, H = 2500, 1686
img = Image.new("RGB", (W, H), "#f0f4f8")
draw = ImageDraw.Draw(img)

# 嘗試載入字體，失敗則用預設
try:
    font_big  = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc",  120)
    font_med  = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc",  70)
    font_icon = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 150)
except Exception:
    font_big  = ImageFont.load_default()
    font_med  = font_big
    font_icon = font_big

cells = [
    # (x1, y1, x2, y2, bg_color, icon, label, sub)
    (0,    0,    1250, 843,  "#27ae60", "✅", "我很好",   "回覆今日打卡"),
    (1250, 0,    2500, 843,  "#e74c3c", "🆘", "需要幫忙", "緊急求助通知"),
    (0,    843,  1250, 1686, "#1a5276", "🤖", "問AI助手", "急救長照問題"),
    (1250, 843,  2500, 1686, "#7f8c8d", "📋", "查看狀態", "目前系統模式"),
]

for x1, y1, x2, y2, bg, icon, label, sub in cells:
    # 背景
    draw.rectangle([x1+8, y1+8, x2-8, y2-8], fill=bg, outline="#ffffff", width=6)

    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2

    # icon
    icon_bbox = draw.textbbox((0,0), icon, font=font_icon)
    iw = icon_bbox[2] - icon_bbox[0]
    draw.text((cx - iw//2, cy - 200), icon, font=font_icon, fill="#ffffff",
              embedded_color=True)

    # label
    lb_bbox = draw.textbbox((0,0), label, font=font_big)
    lw = lb_bbox[2] - lb_bbox[0]
    draw.text((cx - lw//2, cy - 30), label, font=font_big, fill="#ffffff")

    # sub
    sb_bbox = draw.textbbox((0,0), sub, font=font_med)
    sw = sb_bbox[2] - sb_bbox[0]
    draw.text((cx - sw//2, cy + 120), sub, font=font_med, fill="rgba(255,255,255,180)")

# 存檔
img_path = os.path.join(os.path.dirname(__file__), "rich_menu.png")
img.save(img_path, "PNG")
print(f"[OK] Image saved: {img_path}")


# ── 2. 呼叫 LINE API 建立 Rich Menu ──────────
import urllib.request

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type":  "application/json",
}


def line_post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"https://api.line.me{path}",
        data=data, headers=HEADERS, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def line_post_binary(path: str, data: bytes, content_type: str) -> dict:
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type":  content_type,
    }
    req = urllib.request.Request(
        f"https://api-data.line.me{path}",
        data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def line_delete(path: str):
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    req = urllib.request.Request(
        f"https://api.line.me{path}",
        headers=headers, method="DELETE"
    )
    try:
        with urllib.request.urlopen(req):
            pass
    except Exception:
        pass


# 刪除舊的 default rich menu（若存在）
try:
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/richmenu/default",
        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
    )
    with urllib.request.urlopen(req) as r:
        old = json.loads(r.read())
    old_id = old.get("richMenuId", "")
    if old_id:
        line_delete(f"/v2/bot/richmenu/{old_id}")
        print(f"🗑️  已刪除舊 Rich Menu：{old_id}")
except Exception:
    pass

# 建立新的 Rich Menu 定義
rich_menu_body = {
    "size":     {"width": 2500, "height": 1686},
    "selected": True,
    "name":     "鄰里守望選單",
    "chatBarText": "📋 選單",
    "areas": [
        # 我很好
        {
            "bounds": {"x": 0,    "y": 0,   "width": 1250, "height": 843},
            "action": {"type": "message", "label": "我很好", "text": "我很好"},
        },
        # 需要幫忙
        {
            "bounds": {"x": 1250, "y": 0,   "width": 1250, "height": 843},
            "action": {"type": "message", "label": "需要幫忙", "text": "需要幫忙"},
        },
        # 問AI助手
        {
            "bounds": {"x": 0,    "y": 843, "width": 1250, "height": 843},
            "action": {"type": "message", "label": "問AI助手", "text": "幫助"},
        },
        # 查看狀態
        {
            "bounds": {"x": 1250, "y": 843, "width": 1250, "height": 843},
            "action": {"type": "message", "label": "查看狀態", "text": "狀態"},
        },
    ],
}

result = line_post("/v2/bot/richmenu", rich_menu_body)
rich_menu_id = result["richMenuId"]
print(f"✅ Rich Menu 建立：{rich_menu_id}")

# 上傳圖片
with open(img_path, "rb") as f:
    img_data = f.read()

line_post_binary(
    f"/v2/bot/richmenu/{rich_menu_id}/content",
    img_data, "image/png"
)
print("✅ 圖片上傳完成")

# 設為預設
line_post(f"/v2/bot/richmenu/{rich_menu_id}/promote", {})
req2 = urllib.request.Request(
    f"https://api.line.me/v2/bot/richmenu/default",
    data=json.dumps({"richMenuId": rich_menu_id}).encode(),
    headers={**HEADERS},
    method="POST",
)
try:
    with urllib.request.urlopen(req2):
        pass
except Exception:
    # 有些版本 API 路徑不同
    pass

# 直接 link 到所有用戶（另一種設定預設的方式）
req3 = urllib.request.Request(
    f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
    method="POST",
)
try:
    with urllib.request.urlopen(req3):
        print("✅ 已設為所有用戶的預設 Rich Menu")
except Exception as e:
    print(f"⚠️  設預設失敗（可能需要手動設定）：{e}")

print(f"\n🎉 完成！Rich Menu ID：{rich_menu_id}")
print("用戶重新開啟對話即可看到底部選單")
