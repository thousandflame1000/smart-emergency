# -*- coding: utf-8 -*-
"""
驗證 admin.html / index.html 的 esc() HTML escaping helper 存在且正確。

背景：兩個前端頁面過去把使用者可控欄位（長者/志工姓名、地址、需求說明…）
直接塞進 innerHTML 樣板字串，完全沒有逃逸。任何人只要把自己的 LINE 顯示
名稱設成含 <script>/onerror= 的字串，再傳一句話觸發 LINE bot 的自動註冊
流程（app/routers/linebot.py 的 handle_text），該名稱就會被存進
User.name，之後管理員或任何人打開 /admin 或 /（首頁）時就會在瀏覽器裡
執行任意程式碼——而且這兩個頁面完全沒有登入驗證，等於任何人都能透過
LINE 間接對管理後台下手。

這裡不用真的啟動瀏覽器（那個驗證在開發過程中已經用 Playwright + 真實
Edge 手動跑過，確認 payload 不會執行），而是直接用 Node 執行頁面裡的
<script> 區塊，驗證 esc() 函式本身的行為符合預期——這樣至少能在
CI/未來修改時攔住「esc() 被誤刪或改壞」這種回歸。
"""
import subprocess
import tempfile
import os
import pytest

STATIC_DIR = "app/static"


_BROWSER_STUBS = """
// 頁面腳本裡散落著會立即執行的瀏覽器 API 呼叫
// （document.addEventListener(...)、loadAll() 等頁面啟動邏輯），
// 在 Node 裡直接跑會因為 document/fetch 等瀏覽器物件不存在而丟例外。
// 這裡不用去猜要砍哪一段，直接把
// 會被用到的全域物件都填成無害的假物件/no-op，讓整段腳本能跑到底，
// 我們才拿得到 esc() 這個函式宣告來測。
global.document = {
  getElementById: () => ({ addEventListener(){}, classList:{add(){},remove(){},contains(){return false}} }),
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener: () => {},
};
global.window = global;
global.addEventListener = () => {};
global.history = { replaceState(){} };
global.location = { origin:'http://test', hash:'' };
global.fetch = () => Promise.resolve({ json: () => Promise.resolve({}) });
global.setInterval = () => {};
global.confirm = () => true;
global.alert = () => {};
global.prompt = () => null;
"""


def _extract_script(html_path: str) -> str:
    """
    抓出內嵌 <script>（無 src 屬性、獨立一行的那個真正的 tag）的內容。

    這裡踩過兩個坑，都是因為單純用字串 index()／rindex() 找 "<script>"：
    1. 頁面在 <head> 可能有 <script src="...library.js"></script>
       這種外部腳本標籤，直接找第一個 "<script>"／第一個 "</script>"，
       start 會落在後面真正的內嵌腳本，但 end 卻抓到前面外部標籤的
       "</script>"（字串位置更早），導致切出空字串或抓到不對的區間。
    2. 改成找「最後一個」"<script>" 後，又反過來被自己寫的註解坑到——
       esc() 上面的說明文字裡剛好寫了「设成含 <script>/onerror= 的字串」，
       這段純文字裡的 "<script>" 剛好是全文里位置最後的一個，rindex()
       抓到的是這段註解文字中間，不是真正的 tag。

    改成用正規表示式，只認「獨立一行、沒有其他屬性」的 <script> tag。
    """
    import re
    html = open(html_path, encoding="utf-8").read()
    m = re.search(r"(?:^|\n)<script>[ \t]*\n", html)
    if not m:
        raise AssertionError(f"{html_path} 找不到內嵌 <script> 標籤")
    start = m.end()
    end = html.index("</script>", start)
    return html[start:end]


@pytest.mark.parametrize("html_file", ["admin.html", "index.html"])
def test_esc_function_exists_and_escapes_correctly(html_file):
    script = _extract_script(f"{STATIC_DIR}/{html_file}")

    node_script = _BROWSER_STUBS + script + """
    const cases = [
      ['<img src=x onerror=alert(1)>', '&lt;img src=x onerror=alert(1)&gt;'],
      [`it's a "test" & more`, 'it&#39;s a &quot;test&quot; &amp; more'],
      [null, ''],
      [undefined, ''],
      ['plain text', 'plain text'],
    ];
    let ok = true;
    for (const [input, expected] of cases) {
      const got = esc(input);
      if (got !== expected) {
        console.log(JSON.stringify({input, expected, got}));
        ok = false;
      }
    }
    console.log(ok ? 'ALL_PASS' : 'SOME_FAILED');
    """

    # 用檔案跑而不是 `node -e`：頁面腳本加上 stub 動輒 3~4 萬字元，
    # Windows 對單一行程的命令列長度有限制（約 32K），`-e` 的內容是塞在
    # argv 裡的，太長會被靜默截斷，導致 esc() 這種宣告在腳本後段的東西
    # 讀不到——曾經真的因為這樣測試莫名其妙判斷 esc 是 undefined。
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(node_script)
        # Windows 預設用系統 codepage（cp950）解碼 subprocess 輸出，
        # 但腳本裡的中文註解跟 console.log 輸出是 UTF-8，不明講編碼會
        # 直接 UnicodeDecodeError。
        result = subprocess.run(
            ["node", path],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
    finally:
        os.remove(path)

    assert result.returncode == 0, f"node 執行失敗：{result.stderr}"
    assert "ALL_PASS" in result.stdout, f"esc() 行為不符預期：{result.stdout}\n{result.stderr}"


@pytest.mark.parametrize("html_file", ["admin.html", "index.html"])
def test_no_raw_unescaped_user_field_interpolation(html_file):
    """
    粗略靜態檢查：常見的使用者可控欄位（name/address/description/...）
    不應該在樣板字串裡直接以 ${x.field} 形式出現而沒有經過 esc(...) 包裝。
    這是防呆網，不是完整的 AST 分析，抓不到所有寫法，但至少能攔住
    「複製貼上舊寫法忘記加 esc()」這種最常見的回歸。
    """
    import re
    script = _extract_script(f"{STATIC_DIR}/{html_file}")

    risky_fields = ["name", "address", "phone", "description", "note",
                     "quantity", "owner", "elderly", "elderly_name", "contact_name"]
    pattern = re.compile(
        r"\$\{[a-zA-Z_][a-zA-Z0-9_.]*\.(" + "|".join(risky_fields) + r")(\s*\|\|[^}]*)?\}"
    )
    matches = pattern.findall(script)
    assert not matches, f"{html_file} 發現可能未逃逸的使用者欄位輸出：{matches}"
