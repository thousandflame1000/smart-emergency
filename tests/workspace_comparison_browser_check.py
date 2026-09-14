"""Manual comparison workflow check; writes only to the isolated local server."""
import json
import os
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright


def main():
    base = 'http://127.0.0.1:8012'
    graph = {"nodes":[
        {"id":"s", "label":"測試補給站", "kind":"supply", "lat":25.037, "lng":121.56, "quantity":10},
        {"id":"a", "label":"路口甲", "kind":"road_node", "lat":25.037, "lng":121.561},
        {"id":"b", "label":"路口乙", "kind":"road_node", "lat":25.037, "lng":121.565},
        {"id":"p", "label":"測試待援點", "kind":"person", "lat":25.037, "lng":121.566},
    ], "edges":[
        {"id":"entry", "source":"s", "target":"a", "kind":"access"},
        {"id":"road", "label":"測試道路", "source":"a", "target":"b", "kind":"road", "directed":True},
        {"id":"exit", "source":"b", "target":"p", "kind":"access"},
    ]}
    request = Request(base+'/api/workspaces', data=json.dumps({"name":"封路比較驗證", "graph":graph}).encode(), headers={"Content-Type":"application/json"})
    with urlopen(request) as response:
        original = json.load(response)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={"width":1440,"height":960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto(base+'/workspace?id='+original['id'], wait_until='domcontentloaded')
        page.wait_for_function('state.graph.nodes.length === 4')
        page.evaluate("select('edge','road')")
        page.locator('#edit-status').select_option('closed')
        page.locator('#edit-form button[type=submit]').click()
        page.locator('#route-start').select_option('s')
        page.locator('#route-end').select_option('p')
        page.locator('#compare').click()
        page.wait_for_function("state.comparison?.route_delta?.status === 'lost'")
        assert page.evaluate('state.comparison.newly_unreachable_people') == ['p']
        assert page.evaluate("state.baseline.graph.edges.find(e=>e.id==='road').status") == 'normal'
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-comparison-desktop.png'),full_page=True)
        with page.expect_download() as download:
            page.locator('#export-comparison').click()
        report = json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
        assert report['report']['route_delta']['status'] == 'lost'
        assert report['baseline']['graph']['edges'][1]['status'] == 'normal'
        assert report['scenario']['graph']['edges'][1]['status'] == 'closed'

        pending = []
        page.route('**/api/workspaces/compare', lambda route: pending.append(route))
        page.locator('#run-comparison').click()
        page.wait_for_timeout(150)
        assert pending
        page.locator('#compare-end').select_option('b')
        pending[0].fulfill(json=report['report'])
        page.wait_for_timeout(150)
        assert page.evaluate('state.comparison === null')
        assert page.locator('#export-comparison').is_disabled()
        assert page.locator('#run-comparison').is_enabled()
        page.unroute('**/api/workspaces/compare')
        page.locator('#capture-baseline').click()
        assert page.evaluate("state.baseline.graph.edges[1].status") == 'closed'
        page.locator('#close-comparison').click()
        page.locator('#undo').click()
        assert page.evaluate("state.baseline.graph.edges[1].status") == 'normal'
        assert page.evaluate("state.graph.edges[1].status") == 'closed'
        page.locator('#compare').click()
        page.wait_for_function('!!state.comparison')
        page.locator('#copy-scenario').click()
        page.wait_for_function('state.id !== '+json.dumps(original['id'])+' && !state.dirty')
        saved_id = page.evaluate('state.id')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('state.id === '+json.dumps(saved_id))
        assert page.evaluate("state.baseline.graph.edges[1].status") == 'normal'
        page.set_viewport_size({"width":390,"height":844})
        page.locator('#compare').click()
        page.wait_for_function('!!state.comparison')
        page.locator('#compare-start').select_option('s')
        page.locator('#compare-end').select_option('p')
        page.locator('#run-comparison').click()
        page.wait_for_function("state.comparison?.route_delta?.status === 'lost'")
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-comparison-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#comparison-dialog').bounding_box()['width'] <= 390
        assert not errors, errors
        with urlopen(base+'/api/workspaces/'+original['id']) as response:
            unchanged = json.load(response)
        assert unchanged['graph']['edges'][1]['status'] == 'normal'
        print(json.dumps({"status":"passed","original":original['id'],"scenario":saved_id}),flush=True)
        browser.close()


if __name__ == '__main__':
    main()
