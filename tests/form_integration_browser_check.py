"""Browser check for the resident forms, against serve_integrated_workspace.py only.

Run the preview server first (python tests/serve_integrated_workspace.py), then this script.
"""
import json
import os

from playwright.sync_api import sync_playwright

import serve_integrated_workspace  # noqa: F401  shares the preview database and form secret
from app.database import SessionLocal
from app.models.user import User
from app.services.form_token import make_token

BASE_URL = os.getenv("WORKSPACE_PREVIEW_URL", "http://127.0.0.1:8766").rstrip("/")
LINE_UID = "U-form-check"


def resident_token() -> str:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.line_uid == LINE_UID).first():
            db.add(User(name="表單測試居民", roles=["elderly"], line_uid=LINE_UID,
                        lat=24.001, lng=120.601, address="測試地址"))
            db.commit()
    finally:
        db.close()
    return make_token(LINE_UID)


def main():
    token = resident_token()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda r: errors.append(f"{r.status} {r.url}")
                if r.status >= 400 and r.url.startswith(BASE_URL) else None)

        page.goto(f"{BASE_URL}/f/need#t={token}", wait_until="networkidle")
        assert page.locator("#name").input_value() == "表單測試居民"
        assert "t=" not in page.url, "token must be dropped from the address bar"
        page.locator("#need-types input[value=water]").check()
        page.locator("#privacy-ack").check()
        page.locator("#submit").click()
        page.locator("#done").wait_for()
        assert "飲用水" in page.locator("#done-text").inner_text()

        page.goto(f"{BASE_URL}/f/me", wait_until="networkidle")
        assert "飲用水" in page.locator("#me").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

        page.goto(f"{BASE_URL}/f/need#t=abcdefghijklmnop.xyz", wait_until="networkidle")
        assert "過期或無效" in page.locator("#error-top").inner_text()
        assert page.locator("#submit").is_disabled()
        errors = [e for e in errors if not e.startswith("401 ")]  # the expired link is expected to 401

        assert not errors, errors
        print(json.dumps({"result": "passed"}))
        browser.close()


if __name__ == "__main__":
    main()
