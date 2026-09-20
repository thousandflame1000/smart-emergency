# -*- coding: utf-8 -*-
"""LINE 卡片表單：點選就能填完，不用打字。

LINE 沒有真正的表單元件，這裡用 Flex 卡片＋postback 按鈕模擬：每按一下就把選擇存進
對話狀態（獨立的 "form" 命名空間，不和志工申請問卷互相干擾），再回一張更新過的卡片，
已選的項目會打勾。最後按「送出」才真正建立需求或物資。
"""
from __future__ import annotations

NEED_TYPES = [("water", "💧 飲用水"), ("food", "🍱 食物"), ("first_aid", "🩹 藥品／急救"),
              ("shelter", "🏠 庇護所"), ("vehicle", "🚗 交通接送")]
PEOPLE = ["1", "2", "3", "4"]
RES_TYPES = [("water", "💧 飲用水", "水"), ("food", "🍱 食物", "食物"), ("first_aid", "🩹 藥品", "藥品"),
             ("vehicle", "🚗 交通工具", "車"), ("shelter", "🏠 空間", "空間"), ("tool", "🔧 工具", "工具")]
RES_QTY = {"vehicle": ["1台", "2台", "3台"], "shelter": ["1間", "2間", "5間"], "tool": ["1組", "3組", "5組"]}
RES_QTY_DEFAULT = ["10份", "30份", "50份", "100份"]
FORM_NS = "form"
NOTE_PREFIX = "補充："
ADDRESS_PREFIX = "地址："
MAX_TEXT = 100


def _text_field(prefix: str, label: str, value: str | None) -> list[dict]:
    """Not a real textbox (LINE has none in chat): the button opens the keyboard with the
    prefix already typed, and the bot reads the next message that starts with that prefix."""
    shown = [{"type": "text", "text": f"已填：{value}", "size": "sm", "color": "#27ae60", "wrap": True}] if value else []
    return shown + [{
        "type": "button", "height": "sm", "style": "secondary",
        "action": {"type": "postback", "label": ("✏️ 修改" if value else "✏️ ") + label,
                   "data": "action=form&op=noop", "inputOption": "openKeyboard", "fillInText": prefix},
    }]


LINK_TITLES = {"need": "申請物資", "res": "登記可提供物資", "apply": "志工申請"}
_LINK_TEXT = {
    "need": ("📋 申請物資", "#c0392b", "填姓名、地址、需要什麼，志工才找得到您。"),
    "res": ("📦 登記可提供物資", "#148f77", "填物資種類、數量、放在哪裡。"),
    "apply": ("🙋 志工申請", "#2471a3", "謝謝您願意幫忙！填好後管理員會審核並通知您。"),
}


def link_card(kind: str, url: str) -> dict:
    """The card that opens the web form (real text boxes); tap-only fallback below it."""
    title, color, desc = _LINK_TEXT[kind]
    fallback = ({"type": "postback", "label": "⚡ 快速點選（不用打字）", "data": f"action=form&f={kind}&op=open"}
                if kind in ("need", "res") else
                {"type": "postback", "label": "💬 用聊天一題一題回答", "data": "action=form&f=apply&op=wizard"})
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": color,
                   "contents": [{"type": "text", "text": title, "color": "#ffffff", "weight": "bold", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": desc, "wrap": True, "size": "md"},
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "button", "style": "primary", "color": "#27ae60", "height": "md",
             "action": {"type": "uri", "label": "📝 開啟填寫表單", "uri": url}},
            {"type": "button", "style": "secondary", "height": "sm", "action": fallback},
        ]},
    }


def claim_carousel(items: list[dict]) -> dict:
    """items: [{need_id, type, urgency, address, distance_km, description}] -> carousel with a claim button each."""
    bubbles = []
    for it in items:
        lines = [{"type": "text", "text": it["type"], "weight": "bold", "size": "lg"},
                 {"type": "text", "text": f"地點：{it['address'] or '地址未填'}", "wrap": True, "size": "sm"}]
        if it.get("distance_km") is not None:
            lines.append({"type": "text", "text": f"距離約 {it['distance_km']} 公里", "size": "sm", "color": "#555555"})
        if it.get("description"):
            lines.append({"type": "text", "text": it["description"][:60], "wrap": True, "size": "xs", "color": "#888888"})
        urgent = (it.get("urgency") or 0) >= 4
        bubbles.append({
            "type": "bubble", "size": "kilo",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#c0392b" if urgent else "#e67e22",
                       "contents": [{"type": "text", "text": "🔥 緊急" if urgent else "待接單", "color": "#ffffff",
                                     "weight": "bold"}]},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": lines},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "color": "#27ae60",
                 "action": {"type": "postback", "label": "🙋 我來接",
                            "data": f"action=claim&need_id={it['need_id']}"}}]},
        })
    return {"type": "carousel", "contents": bubbles}


def qty_options(rtype: str | None) -> list[str]:
    return RES_QTY.get(rtype, RES_QTY_DEFAULT)


def _btn(label: str, data: str, selected: bool = False, color: str = "#2471a3") -> dict:
    return {
        "type": "button", "flex": 1, "height": "sm",
        "style": "primary" if selected else "secondary",
        **({"color": color} if selected else {}),
        "action": {"type": "postback", "label": ("✅ " if selected else "") + label[:18], "data": data},
    }


def _rows(buttons: list[dict], per_row: int) -> list[dict]:
    return [{"type": "box", "layout": "horizontal", "spacing": "sm", "contents": buttons[i:i + per_row]}
            for i in range(0, len(buttons), per_row)]


def _title(text: str) -> dict:
    return {"type": "text", "text": text, "weight": "bold", "size": "md", "margin": "lg"}


def _shell(header: str, color: str, body: list[dict], submit_label: str, submit_data: str) -> dict:
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": color,
                   "contents": [{"type": "text", "text": header, "color": "#ffffff",
                                 "weight": "bold", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            {"type": "button", "flex": 1, "height": "md", "style": "secondary",
             "action": {"type": "postback", "label": "取消", "data": "action=form&op=cancel"}},
            {"type": "button", "flex": 2, "height": "md", "style": "primary", "color": "#27ae60",
             "action": {"type": "postback", "label": submit_label, "data": submit_data}},
        ]},
    }


def need_card(data: dict) -> dict:
    types = set(data.get("types", []))
    body = [_title("① 需要什麼？（可多選）")]
    body += _rows([_btn(lbl, f"action=form&f=need&op=type&v={k}", k in types) for k, lbl in NEED_TYPES], 2)
    body.append(_title("② 家裡幾個人？"))
    body += _rows([_btn(f"{n}人" if n != "4" else "4人以上", f"action=form&f=need&op=people&v={n}",
                        data.get("people") == n) for n in PEOPLE], 4)
    body.append(_title("③ 有多急？"))
    body += _rows([
        _btn("一般", "action=form&f=need&op=urgent&v=0", not data.get("urgent")),
        _btn("很急", "action=form&f=need&op=urgent&v=1", bool(data.get("urgent")), color="#e74c3c"),
    ], 2)
    body.append(_title("④ 還有什麼想告訴志工？（選填）"))
    body += _text_field(NOTE_PREFIX, "打字補充說明", data.get("note"))
    body.append({"type": "text", "text": "有生命危險請直接撥 119，或按選單的「需要幫忙」。",
                 "size": "xs", "color": "#e74c3c", "wrap": True, "margin": "lg"})
    return _shell("📋 申請物資", "#c0392b", body, "送出申請", "action=form&f=need&op=go")


def resource_card(data: dict) -> dict:
    rtype = data.get("rtype")
    body = [_title("① 您能提供什麼？")]
    body += _rows([_btn(lbl, f"action=form&f=res&op=type&v={k}", rtype == k, color="#148f77")
                   for k, lbl, _kw in RES_TYPES], 2)
    body.append(_title("② 大約多少？"))
    body += _rows([_btn(q, f"action=form&f=res&op=qty&v={q}", data.get("qty") == q, color="#148f77")
                   for q in qty_options(rtype)], 2)
    body.append(_title("③ 物資放在哪裡？（選填）"))
    body += _text_field(ADDRESS_PREFIX, "打字輸入地址", data.get("address"))
    return _shell("📦 登記可提供物資", "#148f77", body, "送出登記", "action=form&f=res&op=go")
