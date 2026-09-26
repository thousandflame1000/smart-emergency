# -*- coding: utf-8 -*-
"""需求狀態在每個畫面都要叫同一個名字。

先前三個前端各自維護一份對照表，結果 suggested 在工作區叫「待核准」、
在後台叫「待確認」，matched 在工作區叫「執行中」、在後台叫「已媒合」。
調度者在畫面之間切換，同一筆需求換一個詞，只能自己猜是不是同一件事
（Nielsen #4：一致性與標準）。

這份測試把前端的 labels.js 與後端的 app/labels.py 綁在一起，任何一邊
改了字而另一邊沒跟上就會被指出來。
"""
import json
import re
from pathlib import Path

import pytest

from app.labels import ALERT_TYPE_ZH, NEED_STATUS_WHY, NEED_STATUS_ZH, alert_type, need_status

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def _js_object(name: str) -> dict:
    """把 labels.js 裡的物件字面量讀成 dict，不另外裝 JS 執行環境。"""
    source = (STATIC / "labels.js").read_text(encoding="utf-8")
    body = re.search(rf"window\.{name}\s*=\s*\{{(.*?)\n\}};", source, re.S)
    assert body, f"labels.js 裡找不到 {name}"
    pairs = re.findall(r"(\w+)\s*:\s*'([^']*)'", body.group(1))
    return dict(pairs)


def test_frontend_and_backend_use_the_same_words():
    assert _js_object("NEED_STATUS_LABEL") == NEED_STATUS_ZH
    assert _js_object("NEED_STATUS_WHY") == NEED_STATUS_WHY


def test_every_status_has_a_label_and_an_explanation():
    from app.models.need import CommunityNeed  # noqa: F401  確保欄位定義被載入

    known = {"open", "suggested", "matched", "fulfilled", "cancelled"}
    assert set(NEED_STATUS_ZH) == known
    assert set(NEED_STATUS_WHY) == known
    for status in known:
        assert NEED_STATUS_ZH[status].strip(), status
        # 說明要講「接下來誰要動作」，不是把標籤重寫一次。
        assert len(NEED_STATUS_WHY[status]) > len(NEED_STATUS_ZH[status]), status


def test_unknown_status_falls_back_without_blanking_the_screen():
    assert need_status("something-new") == "something-new"
    assert need_status(None) == "未知"


def test_alert_labels_match_between_frontend_and_backend():
    assert _js_object("ALERT_TYPE_LABEL") == ALERT_TYPE_ZH


def test_every_alert_the_backend_sends_has_a_chinese_label():
    """總覽頁原本只收了三種警報，unwell 沒收錄就直接把英文印在警報列上。

    這裡從原始碼掃出後端真正送得出來的類型，而不是照抄對照表自己的鍵——
    照抄的話，漏掉的那一種永遠不會被發現。
    """
    root = Path(__file__).resolve().parents[1] / "app"
    emitted = set(re.findall(r"send_alerts_for_checkin\([^,]+,\s*[\"'](\w+)[\"']",
                             (root / "routers" / "linebot.py").read_text(encoding="utf-8")))
    emitted |= set(re.findall(r"alert_type\s*==\s*[\"'](\w+)[\"']",
                              (root / "services" / "alert.py").read_text(encoding="utf-8")))
    assert emitted, "沒有掃到任何警報類型，這個測試本身失效了"
    missing = sorted(emitted - set(ALERT_TYPE_ZH))
    assert not missing, f"後端送得出來但沒有中文說法，畫面會顯示原始代碼：{missing}"


def test_unknown_alert_type_falls_back_without_blanking_the_screen():
    assert alert_type("something-new") == "something-new"
    assert alert_type(None) == "未知"


def test_line_says_the_same_word_as_the_web_for_every_status():
    """LINE 可以多一個圖示，但不能換一個詞。

    先前 LINE 自己留了一份表：matched 在 LINE 是「已派遣」、在後台是「執行中」，
    suggested 在 LINE 是「系統已建議，等待管理員核准」、在後台是「待核准」。
    住戶照著 LINE 的說法打電話問調度者，兩邊講的不是同一個詞。
    """
    from app.routers.linebot import STATUS_ZH

    assert set(STATUS_ZH) == set(NEED_STATUS_ZH)
    for status, canonical in NEED_STATUS_ZH.items():
        assert canonical in STATUS_ZH[status], (
            f"{status} 在 LINE 叫「{STATUS_ZH[status]}」，在網頁叫「{canonical}」")


def test_line_says_the_same_word_as_the_web_for_every_need_type():
    from app.labels import NEED_TYPE_ZH
    from app.routers.linebot import NEED_ZH
    from app.services.dispatch import NEED_TYPE_ZH as DISPATCH_TABLE

    assert DISPATCH_TABLE is NEED_TYPE_ZH, "dispatch.py 又自己抄了一份品項表"
    for kind, canonical in NEED_TYPE_ZH.items():
        assert canonical in NEED_ZH[kind], (
            f"{kind} 在 LINE 叫「{NEED_ZH[kind]}」，在網頁叫「{canonical}」")


def test_every_need_type_the_form_offers_has_a_chinese_label():
    """住戶在 LINE 表單選得到的品項，都要翻得出中文。

    tool 先前只有 dispatch.py 收錄，表單卻把「🔧 工具」排在選項裡，
    於是住戶送出後收到的回覆寫著英文的 tool。
    """
    from app.labels import NEED_TYPE_ZH
    from app.services import line_forms

    source = Path(line_forms.__file__).read_text(encoding="utf-8")
    offered = set(re.findall(r"\(\s*[\"'](\w+)[\"']\s*,\s*[\"'][^\"']*[一-鿿]", source))
    assert offered, "沒有掃到表單選項，這個測試本身失效了"
    missing = sorted(offered - set(NEED_TYPE_ZH))
    assert not missing, f"表單選得到但翻不出中文：{missing}"


# index.html 顯示的是打卡狀態（平安／未回應／等待），跟需求狀態是兩套詞彙，
# 但它會顯示警報類型，所以一樣得引用共用表。
@pytest.mark.parametrize("page", ["admin.html", "workspace.html", "index.html"])
def test_pages_that_show_need_status_load_the_shared_table(page):
    """會顯示需求狀態的畫面都要引用同一份表，不要自己再抄一份。"""
    source = (STATIC / page).read_text(encoding="utf-8")
    assert "labels.js" in source, f"{page} 沒有引用共用的 labels.js"


# 這兩個詞在別的語彙裡是對的，跟需求狀態無關：
#   「數量或單位待確認」講的是匯入資料沒查核過，不是某筆需求在等人核准。
RETIRED_WORDS_ALLOWED = {
    "workspace-operations.js": ["數量或單位待確認"],
}


@pytest.mark.parametrize("script", ["admin.html", "workspace-operations.js",
                                    "workspace-allocation.js", "index.html",
                                    "workspace.html", "workspace.js"])
def test_no_page_keeps_its_own_copy_of_the_status_words(script):
    """再出現一份寫死的說法，就會重蹈三個畫面三種說法的覆轍。

    先前這裡只比對「待確認'」這種緊鄰引號的形式，於是 `>⏳ 待確認<`（篩選按鈕）、
    `待確認 ${suggested.length} 筆`（摘要列）、`stat-tile-label">待確認<`（統計磚）
    十幾處全部溜過去，後台照舊把 suggested 叫「待確認」、matched 叫「已媒合」。
    改成掃整份原始碼，真的有別的意思就寫進 allowlist，而不是放寬比對。
    """
    source = (STATIC / script).read_text(encoding="utf-8")
    for allowed in RETIRED_WORDS_ALLOWED.get(script, []):
        source = source.replace(allowed, "")
    # 註解裡會引用舊說法來解釋當初改了什麼，那是說明不是畫面文字。
    lines = [ln for ln in source.splitlines()
             if not ln.lstrip().startswith(("//", "*", "/*"))]
    body = "\n".join(lines)
    for stale in ("待確認", "已媒合"):
        assert stale not in body, f"{script} 還留著舊說法：{stale}"
