"""LINE Rich Menu：上方有分頁列（像 App 的 nav bar），點分頁直接換成另一張選單，不送訊息。
居民：首頁｜服務。管理員：管理｜首頁｜服務（單帳號示範時不用切帳號就能展示居民功能）。

版面資料是單一來源，`make_rich_menus.py` 拿同一份資料畫圖，這裡拿去建選單，
按鈕文字一定對得上機器人聽得懂的指令。圖檔事先畫好放在 static/richmenu，
因為 Railway 是 Linux，沒有微軟正黑體可以在線上即時畫。
"""
import logging
import os

from linebot.v3.messaging import (
    ApiClient, Configuration, CreateRichMenuAliasRequest, MessagingApi, MessagingApiBlob, PostbackAction,
    RichMenuArea, RichMenuBounds, RichMenuBulkLinkRequest, RichMenuRequest, RichMenuSize, RichMenuSwitchAction,
    UpdateRichMenuAliasRequest, URIAction,
)

from app.config import settings

log = logging.getLogger(__name__)

W, H = 2500, 1686
MENU_NAME_PREFIX = "鄰里守望"
# 成為志工後仍保留同一套居民選單，避免入口位置整張重排。
RESIDENT_NAME = "鄰里守望-首頁"
# 舊版匯入相容；安裝流程會把這張舊選單淘汰。
STAFF_NAME = "鄰里守望-志工"
ADMIN_NAME = "鄰里守望-管理"
IMAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "richmenu")

GREEN, RED, BLUE, ORANGE, GREY, TEAL = "#13795b", "#c9364b", "#2471a3", "#c26a12", "#5f6c65", "#0f766e"

# 每格：(標籤, 副標, 主色, 圖示, 指令)。圖示是 make_rich_menus.py 畫的向量圖名稱；
# 指令以 "tab:" 開頭的是分頁列，點了換成那個別名的選單。
# 首頁＝計畫書寫的三顆按鈕（我很好、需要幫忙、查詢物資），上方整條是 App 入口。
HOME_BODY = [
    [("打開 App", "申請物資・查看進度・急救問答・我的紀錄", GREEN, "app", "打開 App")],
    [("我很好", "回報今天平安", GREEN, "check", "我很好"),
     ("需要幫忙", "緊急時按這裡", RED, "sos", "需要幫忙"),
     ("查詢物資", "附近避難所與物資", TEAL, "compass", "查詢物資")],
]
SERVICES_BODY = [
    [("申請物資", "水、食物、藥品", BLUE, "form", "申請物資"),
     ("查看進度", "需求處理到哪裡", TEAL, "list", "我的需求"),
     ("急救問答", "中風、CPR、跌倒", RED, "chat", "急救問答")],
    [("邀請家人", "有狀況家人會知道", GREEN, "family", "邀請家人"),
     ("我的資料", "電話與地址", GREY, "user", "我的資料"),
     ("我的中心", "志工與家屬功能", ORANGE, "grid", "我的中心")],
]
ADMIN_ROWS = [
    [("決策中心", "全局與待處理量", BLUE, "chart", "決策中心"),
     ("緊急求救", "立即聯繫與處理", RED, "sos", "求救單"),
     ("待派需求", "媒合志工與物資", ORANGE, "box", "待派")],
    [("待審志工", "核准或婉拒申請", TEAL, "userplus", "待審"),
     ("開啟後台", "完整營運資料", GREY, "monitor", "後台"),
     ("查詢物資", "附近避難所與物資", TEAL, "compass", "查詢物資")],
]
RESIDENT_ROWS = HOME_BODY + SERVICES_BODY  # 相容：測試與舊程式用它列出居民會看到的所有按鈕

RESIDENT_TABS = [("首頁", "linri-home"), ("服務", "linri-services")]
ADMIN_TABS = [("管理", "linri-admin"), ("首頁", "linri-admin-home"), ("服務", "linri-admin-services")]
NAV_HEIGHT = 0.13


def _nav(tabs):
    return [(label, "", GREEN, "tab", f"tab:{alias}") for label, alias in tabs]


def _page(alias, tabs, body, image, chat_bar, body_heights):
    return {"alias": alias, "rows": [_nav(tabs)] + body, "heights": [NAV_HEIGHT] + body_heights,
            "image": image, "chat_bar": chat_bar}


_HOME_H, _GRID_H = [0.33, 0.54], [0.435, 0.435]
MENUS = {
    RESIDENT_NAME: _page("linri-home", RESIDENT_TABS, HOME_BODY, "resident.png", "鄰里守望", _HOME_H),
    "鄰里守望-服務": _page("linri-services", RESIDENT_TABS, SERVICES_BODY, "resident-services.png", "鄰里守望", _GRID_H),
    ADMIN_NAME: _page("linri-admin", ADMIN_TABS, ADMIN_ROWS, "admin.png", "決策中心", _GRID_H),
    "鄰里守望-管理員首頁": _page("linri-admin-home", ADMIN_TABS, HOME_BODY, "admin-home.png", "決策中心", _HOME_H),
    "鄰里守望-管理員服務": _page("linri-admin-services", ADMIN_TABS, SERVICES_BODY, "admin-services.png", "決策中心", _GRID_H),
}


# 這些按鈕直接打開 LINE 裡的全螢幕 App（LIFF）對應分頁；其餘是靜默按鈕。
# 「緊急求助」「回報平安」刻意不走網頁：救命與打卡要一按就完成，不能等頁面載入。
LIFF_TABS = {"打開 App": "home", "查詢物資": "nearby", "申請物資": "need", "我的需求": "me", "急救問答": "ask"}


def liff_url(tab: str = "home") -> str | None:
    return f"https://liff.line.me/{settings.LIFF_ID}?go={tab}" if settings.LIFF_ID else None


def menu_action(label: str, command: str):
    if command.startswith("tab:"):
        alias = command[4:]
        # 分頁：直接換選單，LINE 會送一個 postback（switch=）過來，機器人不回話。
        return RichMenuSwitchAction(label=label, rich_menu_alias_id=alias, data=f"switch={alias}")
    url = liff_url(LIFF_TABS[command]) if command in LIFF_TABS else None
    if url:
        return URIAction(label=label, uri=url)
    # 靜默按鈕：按了不會在聊天室冒出一句「我很好」，直接出結果，像 App 而不是對話框。
    return PostbackAction(label=label, data=f"cmd={command}")


def layout(rows, heights=None):
    """回傳 [(x, y, w, h, cell)]。heights 是各列佔的比例（預設平分），列內寬度平分；最後一列補滿，不留縫。"""
    heights = heights or [1 / len(rows)] * len(rows)
    out, y = [], 0
    for r, cells in enumerate(rows):
        h = round(H * heights[r]) if r < len(rows) - 1 else H - y
        col_w = W // len(cells)
        for c, cell in enumerate(cells):
            w = col_w if c < len(cells) - 1 else W - col_w * c
            out.append((c * col_w, y, w, h, cell))
        y += h
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
            action=menu_action(cell[0], cell[4]),
        )
        for x, y, w, h, cell in layout(spec["rows"], spec.get("heights"))
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
    """Only decision makers switch menus; every other role keeps the stable member menu."""
    roles = roles or []
    if "admin" in roles:
        return ADMIN_NAME
    return None


def install_menus(db) -> dict:
    """建立成員與管理選單並完成切換後才移除舊選單。

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
    # 分頁靠別名切換：別名改指到新選單，舊選單之後才刪，切換過程中分頁不會失效。
    for name, spec in MENUS.items():
        alias = spec["alias"]
        try:
            api.update_rich_menu_alias(alias, UpdateRichMenuAliasRequest(rich_menu_id=ids[name]))
        except Exception:
            api.create_rich_menu_alias(CreateRichMenuAliasRequest(rich_menu_alias_id=alias, rich_menu_id=ids[name]))
    api.set_default_rich_menu(ids[RESIDENT_NAME])

    users_by_menu = {ADMIN_NAME: []}
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
