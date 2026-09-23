# -*- coding: utf-8 -*-
"""公開的「掃碼加入」頁：任何人掃 QR Code 就能加入官方帳號，志工可以直接帶著「我要當志工」開始申請。

頁面不含任何個人資料，也不需要登入，所以可以投影在現場、印在海報上。"""
import html
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.services.line_notify import bot_basic_id, oa_message_link
from app.services.qr import svg_data_uri

router = APIRouter()

PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>掃碼加入 鄰里守望</title>
<style>
  :root {{ --bg:#f4f6f8; --card:#fff; --text:#1c2833; --muted:#6b7785; --accent:#c0392b; --green:#038539; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#12171c; --card:#1c242c; --text:#e8edf2; --muted:#98a4b0; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif; }}
  header {{ background:var(--accent); color:#fff; padding:22px 16px; text-align:center; }}
  header h1 {{ margin:0; font-size:28px; }}
  header p {{ margin:6px 0 0; font-size:17px; opacity:.95; }}
  main {{ max-width:900px; margin:0 auto; padding:16px; display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); }}
  .card {{ background:var(--card); border-radius:16px; padding:20px; text-align:center; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  .card h2 {{ margin:0 0 4px; font-size:22px; }}
  .card p {{ margin:0 0 12px; color:var(--muted); font-size:16px; }}
  .qr {{ width:100%; max-width:300px; aspect-ratio:1; background:#fff; padding:10px; border-radius:12px; }}
  .id {{ margin-top:10px; font-size:18px; font-weight:700; }}
  a.btn {{ display:inline-block; margin-top:12px; padding:12px 20px; border-radius:10px; color:#fff; background:var(--green);
           text-decoration:none; font-weight:700; font-size:18px; }}
  .err {{ grid-column:1/-1; background:#fdecea; color:#a93226; padding:16px; border-radius:12px; text-align:center; font-weight:600; }}
</style>
</head>
<body>
<header>
  <h1>鄰里守望</h1>
  <p>用手機相機或 LINE 掃描 QR Code</p>
</header>
<main>
{body}
</main>
</body>
</html>"""

CARD = """<section class="card">
  <h2>{title}</h2>
  <p>{desc}</p>
  <img class="qr" src="{qr}" alt="{title}">
  <div><a class="btn" href="{link}">手機上直接點這裡</a></div>
</section>"""


@router.get("/join", response_class=HTMLResponse, include_in_schema=False)
def join_page():
    basic = bot_basic_id()
    if not basic:
        body = '<div class="err">暫時查不到官方帳號資訊，請稍後重新整理，或直接在 LINE 搜尋「鄰里守望」。</div>'
        return HTMLResponse(PAGE.format(body=body), status_code=200)

    add_friend = f"https://line.me/R/ti/p/{quote(basic)}"
    volunteer = oa_message_link("我要當志工")
    cards = [
        CARD.format(title="居民、長輩加入", desc="掃描後按「加入」，每天早上會收到打卡卡片。",
                    qr=svg_data_uri(add_friend), link=html.escape(add_friend)),
        CARD.format(title="我要當志工", desc="掃描後直接送出訊息，就會開始志工申請。",
                    qr=svg_data_uri(volunteer), link=html.escape(volunteer)),
    ]
    return HTMLResponse(PAGE.format(body="\n".join(cards)))
