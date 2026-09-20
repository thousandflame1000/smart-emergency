"""Browser verification against serve_integrated_workspace.py only."""
import json
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    from serve_integrated_workspace import initialize
    initialize(reset=True)
    artifacts = Path(tempfile.gettempdir()) / "smart-emergency-workspace"
    artifacts.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.route("**/*", lambda route: route.abort() if route.request.method not in ("GET", "HEAD")
                      and not route.request.url.startswith("http://127.0.0.1:8766/") else route.continue_())
        page = context.new_page()
        errors, uploads, failed_requests = [], [], []
        page.on("requestfailed", lambda request: failed_requests.append({"url": request.url, "failure": request.failure}))
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.on("request", lambda request: uploads.append({"url": request.url, "bytes": len(request.post_data or "")})
                if request.method in ("POST", "PUT", "PATCH") else None)
        page.goto("http://127.0.0.1:8766/", wait_until="networkidle")
        frame = page.frame_locator('iframe[data-view="/workspace"]')
        workspace = next(f for f in page.frames if "/workspace" in f.url)
        try:
            workspace.wait_for_function("typeof map !== 'undefined' && !!map && state.graph.nodes.length > 0", timeout=15000)
        except Exception:
            page.screenshot(path=str(artifacts / "load-error.png"), full_page=True)
            print(json.dumps({"errors": errors, "failed_requests": failed_requests,
                              "status": frame.locator("#status").inner_text()}, ensure_ascii=True))
            raise
        choices = page.request.get("http://127.0.0.1:8766/api/workspaces").json()
        frame.locator("#workspace-list").select_option(choices[0]["id"])
        workspace.wait_for_function("state.graph.edges.some(e=>e.id==='road-main')")
        frame.locator("#sync-db").click()
        workspace.wait_for_function("!operationRequest")
        assert not uploads, uploads

        water_id = workspace.evaluate("state.graph.nodes.find(n=>n.properties.resource_type==='water').id")
        frame.locator(f'[data-node="{water_id}"]').click()
        frame.get_by_role("button", name="編輯正式資料").click()
        current_quantity = frame.locator("#inventory-quantity").input_value()
        next_quantity = "19箱" if current_quantity != "19箱" else "20箱"
        frame.locator("#inventory-quantity").fill(next_quantity)
        frame.get_by_role("button", name="加入待寫回").click()
        assert frame.locator("#inventory-count").inner_text() == "1"
        assert not uploads
        frame.locator("#review-inventory").click()
        workspace.wait_for_function("!!inventoryReview")
        assert next_quantity in frame.locator("#inventory-review-result").inner_text()
        frame.locator("#apply-inventory").click()
        workspace.wait_for_function("!inventoryDrafts.size && !operationRequest")
        assert workspace.evaluate("nodeById(" + json.dumps(water_id) + ").properties.quantity_text") == next_quantity
        assert all(u["bytes"] < 1000 for u in uploads), uploads

        owner_id = workspace.evaluate("nodeById(" + json.dumps(water_id) + ").properties.owner_id")
        original_count = workspace.evaluate("state.graph.nodes.filter(n=>n.properties.db==='resource').length")
        workspace.evaluate("mutate(()=>{state.graph.nodes.push({id:'browser-stock',label:'本機新增飲用水',kind:'supply',lat:24.001,lng:120.601,quantity:8,available:true,source:'本機驗證',properties:{},logistics:[]});state.selected={type:'node',id:'browser-stock'};})")
        for index in range(2):
            frame.get_by_role("button", name="登記到物資資料庫").click()
            frame.locator("#inventory-owner").select_option(owner_id)
            frame.locator("#inventory-quantity").fill("8箱")
            if index == 0:
                copied = workspace.evaluate("structuredClone(nodeById('browser-stock'))")
            frame.get_by_role("button", name="加入待寫回").click()
            frame.locator("#review-inventory").click()
            workspace.wait_for_function("!!inventoryReview")
            frame.locator("#apply-inventory").click()
            workspace.wait_for_function("!inventoryDrafts.size && !operationRequest")
            assert workspace.evaluate("state.graph.nodes.filter(n=>n.properties.db==='resource').length") == original_count + 1
            assert workspace.evaluate("state.graph.nodes.length===new Set(state.graph.nodes.map(n=>n.id)).size")
            if index == 0:
                copied["id"] = "browser-stock-copy"
                workspace.evaluate("n=>mutate(()=>{state.graph.nodes.push(n);state.selected={type:'node',id:n.id};})", copied)

        frame.locator("#view-graph").click()
        workspace.wait_for_function("cy && cy.nodes().length > 0")
        page.screenshot(path=str(artifacts / "desktop-graph.png"), full_page=True)
        assert frame.locator("#graph canvas").count() > 0
        workspace.evaluate("select('edge','road-main')")
        frame.locator("#edit-status").select_option("closed")
        frame.locator('#edit-form button[type="submit"]').click()
        page.get_by_role("button", name="總覽", exact=True).click()
        page.get_by_role("button", name="營運工作區", exact=True).click()
        assert workspace.evaluate("state.graph.edges.find(e=>e.id==='road-main').status") == "closed"
        assert "embed=1" in workspace.url and workspace.evaluate("state.dirty")
        frame.locator("#undo").click()
        assert workspace.evaluate("state.graph.edges.find(e=>e.id==='road-main').status") == "normal"

        frame.locator('[data-stage="open"]').click()
        water_need = workspace.evaluate("operationalNodes().find(n=>n.properties.need_type==='water'&&n.properties.status==='open')?.id")
        if water_need:
            frame.locator(f'[data-task="{water_need}"]').click()
            assert frame.locator(".related-objects button").count() > 0
            frame.locator("#allocation").click()
            frame.locator("#allocation-material").select_option(label="飲用水 / 箱")
            frame.locator("#allocation-compare").uncheck()
            frame.locator("#run-allocation").click()
            workspace.wait_for_function("!!state.allocation")
            frame.locator("#send-allocation").click()
            frame.locator("#review-dispatch").click()
            workspace.wait_for_function("!operationRequest && operationStage==='suggested'")
            frame.locator(f'[data-task="{water_need}"]').click()
            frame.get_by_role("button", name="核准派遣", exact=True).click()
            workspace.wait_for_function("!operationRequest && nodeById(" + json.dumps(water_need) + ").properties.status==='matched'")

        frame.locator("#view-map").click()
        frame.locator('[data-stage="matched"]').click()
        workspace.wait_for_function("document.querySelectorAll('.leaflet-tile-loaded').length > 0")
        page.wait_for_timeout(500)
        page.screenshot(path=str(artifacts / "desktop-map.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert workspace.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Workspace mobile overflow"
        page.screenshot(path=str(artifacts / "mobile-map.png"), full_page=True)
        frame.locator("#view-graph").click()
        assert workspace.evaluate("cy.nodes().length") > 0
        page.screenshot(path=str(artifacts / "mobile-graph.png"), full_page=True)
        frame.locator("#selection").scroll_into_view_if_needed()
        page.screenshot(path=str(artifacts / "mobile-detail.png"), full_page=True)
        assert not errors, errors
        print(json.dumps({"result": "passed", "screenshots": str(artifacts), "uploads": uploads}, ensure_ascii=True))
        browser.close()


if __name__ == "__main__":
    main()
