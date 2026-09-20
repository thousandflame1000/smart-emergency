"""LINE Rich Menu：一般民眾一張、志工／家屬／管理員一張。

版面資料是單一來源，`make_rich_menus.py` 拿同一份資料畫圖，這裡拿去建選單，
按鈕文字一定對得上機器人聽得懂的指令。圖檔事先畫好放在 static/richmenu，
因為 Railway 是 Linux，沒有微軟正黑體可以在線上即時畫。
"""
import logging
import os

from linebot.v3.messaging import (
    ApiClient, Configuration, MessageAction, MessagingApi, MessagingApiBlob,
    RichMenuArea, RichMenuBounds, RichMenuRequest, RichMenuSize,
)

from app.config import settings

log = logging.getLogger(__name__)

W, H = 2500, 1686
MENU_NAME_PREFIX = "鄰里守望"
RESIDENT_NAME = "鄰里守望-一般"
STAFF_NAME = "鄰里守望-志工"
STAFF_ROLES = ("volunteer", "family", "admin")
IMAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "richmenu")

GREEN, RED, BLUE, ORANGE, GREY, TEAL = "#27ae60", "#e74c3c", "#2471a3", "#e67e22", "#7f8c8d", "#148f77"

# 每格：(標籤, 副標, 底色, 圖示, 送出的文字)。一列一個 list，格子平分該列寬度。
RESIDENT_ROWS = [
    [("我很好", "回報今日平安", GREEN, "✅", "我很好"),
     ("需要幫忙", "緊急求助", RED, "🆘", "需要幫忙")],
    [("需要水", "飲用水", BLUE, "💧", "需要水"),
     ("需要食物", "糧食", ORANGE, "🍱", "需要食物"),
     ("需要藥品", "藥品／急救", TEAL, "🩹", "需要藥")],
    [("我的需求", "看處理進度", GREY, "📋", "我的需求"),
     ("分享位置", "讓志工找到您", GREY, "📍", "分享位置"),
     ("全部功能", "指令說明", GREY, "❓", "幫助")],
]
STAFF_ROWS = [
    [("我很好", "回報今日平安", GREEN, "✅", "我很好"),
     ("需要幫忙", "緊急求助", RED, "🆘", "需要幫忙")],
    [("登記物資", "我能提供什麼", BLUE, "📦", "登記物資"),
     ("我的物資", "已登記項目", TEAL, "🧾", "我的物資"),
     ("取消物資", "撤回未媒合", ORANGE, "↩️", "取消物資")],
    [("我的需求", "看處理進度", GREY, "📋", "我的需求"),
     ("分享位置", "更新我的位置", GREY, "📍", "分享位置"),
     ("全部功能", "指令說明", GREY, "❓", "幫助")],
]
MENUS = {
    RESIDENT_NAME: {"rows": RESIDENT_ROWS, "image": "resident.png", "chat_bar": "📋 需要什麼？點我"},
    STAFF_NAME: {"rows": STAFF_ROWS, "image": "staff.png", "chat_bar": "📋 志工選單"},
}


def layout(rows):
    """回傳 [(x, y, w, h, cell)]，每列高度平分、列內寬度平分。"""
    out = []
    row_h = H // len(rows)
    for r, cells in enumerate(rows):
        col_w = W // len(cells)
        for c, cell in enumerate(cells):
            w = col_w if c < len(cells) - 1 else W - col_w * c
            out.append((c * col_w, r * row_h, w, row_h, cell))
    return out


def _apis():
    client = ApiClient(Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN))
    return MessagingApi(client), MessagingApiBlob(client)


def _request(name: str) -> RichMenuRequest:
    spec = MENUS[name]
    areas = [
        RichMenuArea(
            bounds=RichMenuBounds(x=x, y=y, width=w, height=h),
            action=MessageAction(label=cell[0], text=cell[4]),
        )
        for x, y, w, h, cell in layout(spec["rows"])
    ]
    return RichMenuRequest(
        size=RichMenuSize(width=W, height=H), selected=True, name=name,
        chat_bar_text=spec["chat_bar"], areas=areas,
    )


def _menu_id(api: MessagingApi, name: str) -> str | None:
    for m in api.get_rich_menu_list().richmenus or []:
        if m.name == name:
            return m.rich_menu_id
    return None


def is_staff(roles) -> bool:
    return bool(roles) and any(r in roles for r in STAFF_ROLES)


def install_menus(db) -> dict:
    """刪掉舊的鄰里守望選單，重建兩張、一般版設為預設、志工版綁給現有志工。"""
    from app.models.user import User

    api, blob = _apis()
    removed = 0
    for m in api.get_rich_menu_list().richmenus or []:
        if (m.name or "").startswith(MENU_NAME_PREFIX):
            api.delete_rich_menu(m.rich_menu_id)
            removed += 1

    ids = {}
    for name, spec in MENUS.items():
        menu_id = api.create_rich_menu(_request(name)).rich_menu_id
        with open(os.path.join(IMAGE_DIR, spec["image"]), "rb") as f:
            blob.set_rich_menu_image(menu_id, body=bytearray(f.read()),
                                     _headers={"Content-Type": "image/png"})
        ids[name] = menu_id
    api.set_default_rich_menu(ids[RESIDENT_NAME])

    linked, failed = 0, 0
    for user in db.query(User).filter(User.line_uid.isnot(None), User.is_active == True).all():  # noqa: E712
        if not is_staff(user.roles):
            continue
        try:
            api.link_rich_menu_id_to_user(user.line_uid, ids[STAFF_NAME])
            linked += 1
        except Exception:
            log.warning("link staff menu failed for %s", user.id, exc_info=True)
            failed += 1
    return {"removed_old": removed, "menus": ids, "staff_linked": linked, "staff_link_failed": failed}


def sync_user_menu(user) -> None:
    """角色變動後，讓這個人看到對的選單。失敗不影響主流程（沒選單也能打字）。"""
    if not user or not user.line_uid:
        return
    try:
        api, _ = _apis()
        if is_staff(user.roles):
            menu_id = _menu_id(api, STAFF_NAME)
            if menu_id:
                api.link_rich_menu_id_to_user(user.line_uid, menu_id)
        else:
            api.unlink_rich_menu_id_from_user(user.line_uid)
    except Exception:
        log.warning("sync rich menu failed for %s", getattr(user, "id", "?"), exc_info=True)
