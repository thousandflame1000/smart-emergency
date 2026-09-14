"""Manual local-only inventory entry, flow planning, route, persistence and mobile check."""
import json
import os
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright


def main():
    base = 'http://127.0.0.1:8012'
    graph = {"nodes":[
        {"id":"s", "label":"測試供應位置", "kind":"facility", "quantity":0, "lat":25.037, "lng":121.56},
        {"id":"a", "label":"路口甲", "kind":"road_node", "lat":25.037, "lng":121.561},
        {"id":"b", "label":"路口乙", "kind":"road_node", "lat":25.037, "lng":121.565},
        {"id":"d", "label":"測試需求位置", "kind":"person", "lat":25.037, "lng":121.566},
    ], "edges":[
        {"id":"entry", "source":"s", "target":"a", "kind":"access"},
        {"id":"road", "source":"a", "target":"b", "kind":"road", "directed":True},
        {"id":"exit", "source":"b", "target":"d", "kind":"access"},
    ]}
    with urlopen(Request(base+'/api/workspaces', data=json.dumps({"name":"物資分配驗證", "graph":graph}).encode(), headers={"Content-Type":"application/json"})) as response:
        original = json.load(response)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page(viewport={"width":1440,"height":960})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto(base+'/workspace?id='+original['id'], wait_until='domcontentloaded')
        page.wait_for_function('state.graph.nodes.length === 4')
        page.locator('#allocation').click()
        assert page.locator('#allocation-stock [data-logistics]').count() == 0
        page.locator('#add-stock').click()
        page.locator('[data-allocation-node="s"]').click()
        stock = page.locator('#allocation-stock')
        for field, value in [('item','飲用水'),('unit','箱'),('quantity','10'),('dispatch_limit','6')]:
            stock.locator('[data-log-field="'+field+'"]').fill(value)
            stock.locator('[data-log-field="'+field+'"]').press('Tab')
        page.locator('#add-demand').click()
        page.locator('[data-allocation-node="d"]').click()
        demand = page.locator('#allocation-demand')
        demand.locator('[data-log-field="quantity"]').fill('8')
        demand.locator('[data-log-field="quantity"]').press('Tab')
        demand.locator('[data-log-field="priority"]').select_option('5')
        page.locator('#allocation-baseline').click()
        page.locator('#run-allocation').click()
        page.wait_for_function('state.allocation?.after.summary.allocated === 6')
        assert page.evaluate('state.allocation.after.summary.unmet') == 2
        assert page.evaluate('state.allocation.before.summary.allocated') == 6
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-allocation-desktop.png'),full_page=True)
        with page.expect_download() as download:
            page.locator('#allocation-export').click()
        report = json.loads(Path(download.value.path()).read_text(encoding='utf-8'))
        assert report['report']['after']['inventory'][0]['remaining'] == 4
        assert report['inputs']['graph']['nodes'][0]['logistics'][0]['quantity'] == 10
        page.locator('[data-allocation-route="0"]').click()
        assert page.evaluate('state.report.route.edges') == ['entry','road','exit']
        page.locator('#allocation').click()
        page.locator('#allocation-minutes').fill('0.1')
        assert page.locator('#allocation-export').is_disabled()
        assert page.evaluate('state.report === null')
        page.locator('#run-allocation').click()
        page.wait_for_function("state.allocation?.after.demands[0].reason === 'travel_limit'")
        page.locator('#allocation-minutes').fill('120')
        page.locator('#allocation-copy').click()
        page.wait_for_function('state.id !== '+json.dumps(original['id'])+' && !state.dirty')
        saved_id = page.evaluate('state.id')
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('state.id === '+json.dumps(saved_id))
        assert page.evaluate('logisticsRows().length') == 2
        assert page.evaluate('state.baseline.graph.nodes[0].logistics[0].quantity') == 10
        page.evaluate("select('edge','road')")
        page.locator('#edit-status').select_option('closed')
        page.locator('#edit-form button[type=submit]').click()
        page.set_viewport_size({"width":390,"height":844})
        page.locator('#allocation').click()
        page.locator('#run-allocation').click()
        page.wait_for_function("state.allocation?.after.demands[0].reason === 'unreachable'")
        assert page.evaluate('state.allocation.before.summary.allocated') == 6
        page.locator('#allocation-result').scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-allocation-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.evaluate("document.getElementById('allocation-dialog').scrollWidth <= document.getElementById('allocation-dialog').clientWidth")
        assert not errors, errors
        with urlopen(base+'/api/workspaces/'+original['id']) as response:
            unchanged = json.load(response)
        assert not unchanged['graph']['nodes'][0]['logistics']
        print(json.dumps({"status":"passed","saved_id":saved_id,"allocated_before":6,"allocated_after_closure":0}),flush=True)
        browser.close()


if __name__ == '__main__':
    main()
