"""LINE Rich Menu：居民、志工、決策者各一張。

版面資料是單一來源，`make_rich_menus.py` 拿同一份資料畫圖，這裡拿去建選單，
按鈕文字一定對得上機器人聽得懂的指令。圖檔事先畫好放在 static/richmenu，
因為 Railway 是 Linux，沒有微軟正黑體可以在線上即時畫。
"""
import logging
import os

from linebot.v3.messaging import (
    ApiClient, Configuration, MessageAction, MessagingApi, MessagingApiBlob,
    RichMenuArea, RichMenuBounds, RichMenuBulkLinkRequest, RichMenuRequest, RichMenuSize,
)

from app.config import settings

log = logging.getLogger(__name__)

W, H = 2500, 1686
MENU_NAME_PREFIX = "鄰里守望"
RESIDENT_NAME = "鄰里守望-一般"
STAFF_NAME = "鄰里守望-志工"
ADMIN_NAME = "鄰里守望-管理員"
IMAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "richmenu")

GREEN, RED, BLUE, ORANGE, GREY, TEAL = "#27ae60", "#e74c3c", "#2471a3", "#e67e22", "#7f8c8d", "#148f77"

# 每格：(標籤, 副標, 底色, 圖示, 送出的文字)。一列一個 list，格子平分該列寬度。
# 主選單只放角色在當下最常做的第一步；延伸資訊在「中心」卡片中依情境展開。
# 家屬是居民照護關係的一種，不再維護第四張固定選單，避免分流與重複操作。
RESIDENT_ROWS = [
    [("緊急求助", "危險時先選這裡", RED, "🆘", "需要幫忙"),
     ("申請需求", "一次填好所需項目", ORANGE, "📝", "申請物資"),
     ("查看進度", "追蹤目前處理狀況", BLUE, "📋", "我的需求")],
    [("回報平安", "今天狀況良好", GREEN, "✅", "我很好"),
     ("分享位置", "讓協助者找到您", TEAL, "📍", "分享位置"),
     ("居民中心", "家屬、紀錄與說明", GREY, "📁", "居民中心")],
]
STAFF_ROWS = [
    [("接單", "查看附近待協助事項", ORANGE, "🙋", "接單"),
     ("我的任務", "回報進行中的任務", BLUE, "🚚", "我的任務"),
     ("登記物資", "登記可提供的資源", TEAL, "📦", "登記物資")],
    [("志工中心", "物資、位置與操作說明", GREY, "📁", "志工中心"),
     ("分享位置", "更新服務位置", TEAL, "📍", "分享位置"),
     ("需要幫忙", "志工自身緊急求助", RED, "🆘", "需要幫忙")],
]
ADMIN_ROWS = [
    [("決策中心", "先看全局與待處理量", BLUE, "📊", "決策中心"),
     ("緊急求救", "立即聯繫與處理", RED, "🆘", "求救單"),
     ("待派需求", "媒合志工與物資", ORANGE, "📦", "待派")],
    [("待審志工", "核准或婉拒申請", TEAL, "🙋", "待審"),
     ("開啟後台", "查看完整營運資料", GREY, "🔐", "後台"),
     ("操作說明", "查詢完整指令與規則", GREY, "?", "幫助")],
]
MENUS = {
    RESIDENT_NAME: {"rows": RESIDENT_ROWS, "image": "resident.png", "chat_bar": "居民服務"},
    STAFF_NAME: {"rows": STAFF_ROWS, "image": "staff.png", "chat_bar": "志工中心"},
    ADMIN_NAME: {"rows": ADMIN_ROWS, "image": "admin.png", "chat_bar": "決策中心"},
}


def layout(rows):
    """回傳 [(x, y, w, h, cell)]，每列高度平分、列內寬度平分。"""
    out = []
    row_h = H // len(rows)
    for r, cells in enumerate(rows):
        h = row_h if r < len(rows) - 1 else H - row_h * r
        col_w = W // len(cells)
        for c, cell in enumerate(cells):
            w = col_w if c < len(cells) - 1 else W - col_w * c
            out.append((c * col_w, r * row_h, w, h, cell))
    return out


def _apis():
    client = ApiClient(Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN))
    send = client.rest_client.request
    client.rest_client.request = lambda *a, _request_timeout=None, **kw: send(
        *a, _request_timeout=_request_timeout or (5.0, 30.0), **kw)
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


def menu_name_for(roles) -> str | None:
    """Which primary menu a person should see: decision maker over volunteer over resident."""
    roles = roles or []
    if "admin" in roles:
        return ADMIN_NAME
    if "volunteer" in roles:
        return STAFF_NAME
    return None


def install_menus(db) -> dict:
    """建立三張新選單並完成切換後才移除舊選單。

    角色綁定失敗時保留舊選單，讓維運人員可安全重試，不會先清空正式入口。
    """
    from app.models.user import User

    api, blob = _apis()
    old_menus = [
        m for m in api.get_rich_menu_list().richmenus or []
        if (m.name or "").startswith(MENU_NAME_PREFIX)
    ]

    ids = {}
    for name, spec in MENUS.items():
        menu_id = api.create_rich_menu(_request(name)).rich_menu_id
        with open(os.path.join(IMAGE_DIR, spec["image"]), "rb") as f:
            blob.set_rich_menu_image(menu_id, body=bytearray(f.read()),
                                     _headers={"Content-Type": "image/png"})
        ids[name] = menu_id
    api.set_default_rich_menu(ids[RESIDENT_NAME])

    users_by_menu = {STAFF_NAME: [], ADMIN_NAME: []}
    for user in db.query(User).filter(User.line_uid.isnot(None), User.is_active == True).all():  # noqa: E712
        name = menu_name_for(user.roles)
        if name:
            users_by_menu[name].append(user.line_uid)

    linked, failed = 0, 0
    for name, user_ids in users_by_menu.items():
        for start in range(0, len(user_ids), 500):
            batch = user_ids[start:start + 500]
            try:
                api.link_rich_menu_id_to_users(
                    RichMenuBulkLinkRequest(rich_menu_id=ids[name], user_ids=batch)
                )
                linked += len(batch)
            except Exception:
                log.warning("bulk link menu failed for %s users", len(batch), exc_info=True)
                failed += len(batch)

    removed = 0
    if failed == 0:
        for menu in old_menus:
            try:
                api.delete_rich_menu(menu.rich_menu_id)
                removed += 1
            except Exception:
                log.warning("delete old menu failed for %s", menu.rich_menu_id, exc_info=True)

    return {"removed_old": removed, "menus": ids, "role_linked": linked, "role_link_failed": failed,
            "cutover_complete": failed == 0,
            # Kept for callers from the previous release.
            "staff_linked": linked, "staff_link_failed": failed}


def sync_user_menu(user) -> None:
    """角色變動後，讓這個人看到對的選單。失敗不影響主流程（沒選單也能打字）。"""
    if not user or not user.line_uid:
        return
    try:
        api, _ = _apis()
        name = menu_name_for(user.roles)
        menu_id = _menu_id(api, name) if name else None
        if menu_id:
            api.link_rich_menu_id_to_user(user.line_uid, menu_id)
        else:
            api.unlink_rich_menu_id_from_user(user.line_uid)
    except Exception:
        log.warning("sync rich menu failed for %s", getattr(user, "id", "?"), exc_info=True)
