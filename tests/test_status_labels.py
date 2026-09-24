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

from app.labels import NEED_STATUS_WHY, NEED_STATUS_ZH, need_status

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


# index.html 顯示的是打卡狀態（平安／未回應／等待），跟需求狀態是兩套詞彙，不在此列。
@pytest.mark.parametrize("page", ["admin.html", "workspace.html"])
def test_pages_that_show_need_status_load_the_shared_table(page):
    """會顯示需求狀態的畫面都要引用同一份表，不要自己再抄一份。"""
    source = (STATIC / page).read_text(encoding="utf-8")
    assert "labels.js" in source, f"{page} 沒有引用共用的 labels.js"


@pytest.mark.parametrize("script", ["admin.html", "workspace-operations.js"])
def test_no_page_keeps_its_own_copy_of_the_status_words(script):
    """再出現一份寫死的對照表，就會重蹈三個畫面三種說法的覆轍。"""
    source = (STATIC / script).read_text(encoding="utf-8")
    for stale in ("待確認'", "待確認\"", "已媒合'", "已媒合\""):
        assert stale not in source, f"{script} 還留著舊說法：{stale}"
