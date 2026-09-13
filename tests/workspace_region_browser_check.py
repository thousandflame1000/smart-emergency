"""Exercise region loading and reopening against an isolated local database."""
import json
import os
import tempfile

from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width":1440,"height":960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8012/workspace')
        page.locator('#region-dialog').wait_for(state='visible')
        page.locator('#region-query').fill('臺北市')
        page.locator('#region-search-button').click()
        page.wait_for_function('regionPlace !== null', timeout=25000)
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-region-picker.png'),full_page=True)
        page.locator('#region-radius').select_option('250')
        page.route('**/api/workspaces/openstreetmap', lambda route: route.fulfill(
            json={"graph":{"nodes":[],"edges":[]},"provider":"test"}))
        page.locator('#region-load').click()
        page.wait_for_function("document.getElementById('region-status').textContent.includes('沒有可載入')")
        assert page.evaluate('state.id === null')
        assert page.locator('#region-dialog').is_visible()
        page.unroute('**/api/workspaces/openstreetmap')
        page.locator('#region-load').click()
        page.wait_for_function('state.id && state.graph.nodes.length > 0', timeout=90000)
        page.locator('#region-dialog').wait_for(state='hidden')
        counts = page.evaluate("({roads:state.graph.nodes.filter(n=>n.kind==='road_node').length,facilities:state.graph.nodes.filter(n=>n.kind==='facility').length})")
        assert counts['roads'] > 0 and counts['facilities'] > 0
        assert page.evaluate("state.graph.nodes.filter(n=>n.kind==='facility').every(n=>n.quantity===0)")
        saved_id = page.evaluate('state.id')
        assert 'id='+saved_id in page.url
        page.goto('http://127.0.0.1:8012/workspace')
        page.wait_for_function('state.id === '+json.dumps(saved_id))
        assert not page.locator('#region-dialog').is_visible()
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-region-loaded.png'),full_page=True)
        page.locator('[data-layer-add="person"]').click()
        assert page.locator('#import-kind').input_value() == 'person'
        page.locator('#close-import').click()
        page.set_viewport_size({"width":390,"height":844})
        page.locator('#region').click()
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-region-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('#region-load').is_visible()
        assert not errors, errors
        print(json.dumps({"status":"passed", "counts":counts, "saved_id":saved_id}),flush=True)
        browser.close()


if __name__ == '__main__':
    main()
