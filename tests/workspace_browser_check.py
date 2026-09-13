"""Manual end-to-end smoke: run against an isolated local development database."""
import json
import os
import tempfile

from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page(viewport={"width":1440, "height":960})
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto("http://127.0.0.1:8012/workspace", wait_until="networkidle")
        page.wait_for_function("typeof map !== 'undefined' && !!map", timeout=45000)

        def upload(name, text, kind):
            page.locator('#import').click()
            page.locator('#import-file').set_input_files({"name":name, "mimeType":"text/plain", "buffer":text.encode()})
            page.wait_for_function("document.getElementById('import-content').value.length > 0")
            page.locator('#import-kind').select_option(kind)
            page.locator('#preview-import').click()
            page.locator('#apply-import').wait_for(state="visible")
            page.wait_for_function("!document.getElementById('apply-import').disabled")
            page.locator('#apply-import').click()

        geo = {"type":"FeatureCollection", "features":[{"type":"Feature", "properties":{"name":"外地路網"},
               "geometry":{"type":"LineString", "coordinates":[[139,35],[139.005,35],[139.01,35]]}}]}
        upload('roads.geojson', json.dumps(geo), 'road_node')
        upload('people.csv', '編號,名稱,緯度,經度\np1,待援人員,35,139.0101\n', 'person')
        upload('stock.csv', '編號,名稱,緯度,經度,庫存\ns1,供應站,35,138.9999,10\n', 'supply')
        page.locator('#access').click()
        page.locator('#route-start').select_option('s1')
        page.locator('#route-end').select_option('p1')
        page.locator('#route').click()
        page.wait_for_function("state.report?.route?.reachable === true")
        before = page.locator('#route-result').inner_text()
        page.locator('#view-graph').click()
        page.evaluate("select('edge', state.graph.edges.find(e=>e.kind==='road').id)")
        page.locator('#edit-status').select_option('closed')
        page.locator('#edit-form button[type=submit]').click()
        page.locator('#route').click()
        page.wait_for_function("state.report?.route?.reachable === false")
        assert page.evaluate('state.report.metrics.unreachable_people') == 1
        page.locator('#undo').click()
        page.locator('#route').click()
        page.wait_for_function("state.report?.route?.reachable === true")
        page.locator('#workspace-name').fill('跨地區匯入驗證')
        page.locator('#save').click()
        page.wait_for_function("state.id && !state.dirty")
        url = page.url
        page.reload(wait_until='networkidle')
        page.wait_for_function("state.graph.nodes.length === 5")
        assert page.evaluate('state.graph.edges.length') == 4
        page.locator('#view-graph').click()
        page.wait_for_function('cy && cy.nodes().length === 5')
        graph_image = os.path.join(tempfile.gettempdir(), 'workspace-graph-desktop.png')
        page.screenshot(path=graph_image, full_page=True)
        page.locator('#view-map').click()
        page.locator('#analyze').click()
        page.wait_for_function('!!state.report')
        desktop_image = os.path.join(tempfile.gettempdir(), 'workspace-map-desktop.png')
        page.screenshot(path=desktop_image, full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(tempfile.gettempdir(),'workspace-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow'
        page.locator('#view-graph').click()
        assert page.locator('#graph').is_visible()
        assert page.evaluate('cy.nodes().length') == 5
        assert not errors, errors
        print(json.dumps({"result":"passed", "saved_url":url, "baseline_route":before,
                          "screenshots":[graph_image,desktop_image,os.path.join(tempfile.gettempdir(),'workspace-mobile.png')]}, ensure_ascii=False))
        browser.close()


if __name__ == '__main__':
    main()
