#!/usr/bin/env python3
"""Real browser rendering against an explicitly TEST-ONLY service adapter.

No native FalkorDB, SDK MCP, HTTP backend, or model/provider is exercised here.
The JavaScript uses a local test fetch binding, as in Stage 1.1 browser checks.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from stage11_support import service_modules
from playwright.sync_api import sync_playwright


def main():
    output=Path(os.environ.get('HOLDOUT_UI_QA_DIR',str(ROOT/'.runtime/stage2-ui')))
    output.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='holdout-s2-ui-') as td,service_modules() as svc:
        work=Path(td);s=svc.HoldoutService(ROOT/'fixtures/decision-impact/dataset.json',work/'one')
        # Seed known evidence through actual service tools, not injected verdicts.
        for cid in ('a-x','a-y','b-y'):s.investigate(cid,'exact')
        def current():
            result=s.state();result['provider_available']=True
            result['engine']['name']='TEST-ONLY UI adapter'
            return result
        def handle(url,body=None):
            path=urlparse(url).path;payload=json.loads(body) if body else {}
            try:
                if path=='/api/state':return current()
                if path.startswith('/api/candidates/'):return s.candidate_detail(path.rsplit('/',1)[1])
                if path=='/api/plan':s.plan_investigation(**payload);return current()
                if path=='/api/investigate':s.investigate(**payload);return current()
                if path=='/api/release':s.stage_release(**payload);return current()
                if path=='/api/plan/report':return s.planning_report()
                if path=='/api/run':
                    assert payload['mode']=='graph_guided'
                    for _ in range(12):
                        p=s.plan_investigation()
                        if p['stop']['stop']:break
                        s.investigate(p['next_candidate']['id'],'auto')
                    s.stage_release();s.agent.update(status='completed',mode='graph_guided',message='TEST-ONLY driver: UI/service contracts, not native MCP.')
                    return current()
                raise AssertionError('Unexpected URL '+path)
            except Exception as e:
                errors.append(type(e).__name__+': '+str(e));return {'error':str(e)}
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
                page.on('pageerror',lambda e:errors.append(str(e)))
                html=(ROOT/'dist/index.html').read_text();html=re.sub(r'<script[^>]*src=[^>]*></script>','',html);html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*>','',html)
                page.set_content(html);page.add_style_tag(content=(ROOT/'dist/styles.css').read_text())
                page.expose_function('__holdoutTestBackend',handle)
                page.add_script_tag(content="window.fetch = async (url, opts={}) => new Response(JSON.stringify(await window.__holdoutTestBackend(String(url),opts.body||null)), {status:200,headers:{'Content-Type':'application/json'}});")
                page.add_script_tag(content=(ROOT/'dist/app.js').read_text())
                page.locator('#llm-button').wait_for(state='visible')
                assert 'primary' in page.locator('#llm-button').get_attribute('class')
                assert 'secondary' in page.locator('#run-button').get_attribute('class')
                checks.append('Live agent remains first primary action; deterministic baseline secondary')
                assert 'Not calculated' in page.locator('#plan-badge').inner_text()
                assert page.locator('#plan-download').is_disabled()
                checks.append('No counterfeit precomputed priorities or report')
                page.click('#plan-button');page.wait_for_timeout(200)
                assert 'high' in page.locator('.plan-recommended').inner_text()
                assert 'save 8' in page.locator('.plan-recommended').inner_text()
                assert 'redundant' in page.locator('.plan-table').inner_text()
                assert s.candidate('high')['status']=='unresolved'
                assert page.locator('#plan-download').is_enabled()
                checks.append('8-unit counterfactual saving displayed separately from evidence; report enabled')
                page.locator('#planning-section').scroll_into_view_if_needed();page.screenshot(path=str(output/'planner-desktop.png'))
                # A one-claim scan must visibly disclose incomplete coverage.
                page.fill('#plan-cap','1');page.click('#plan-button');page.wait_for_timeout(100)
                assert 'Partial scan' in page.locator('#plan-results').inner_text()
                checks.append('Partial coverage is visible, never presented as global-best ranking')
                page.fill('#plan-cap','32');page.click('#plan-button');page.wait_for_timeout(100)
                page.locator('.plan-recommended [data-candidate]').click();page.wait_for_timeout(100)
                page.click('#investigate-selected');page.wait_for_timeout(100)
                assert 'Not calculated' in page.locator('#plan-badge').inner_text()
                assert page.locator('#plan-download').is_disabled()
                checks.append('Real evidence action invalidates old priorities and report')
                page.click('#plan-button');page.wait_for_timeout(100)
                assert 'Cost-stable' in page.locator('#plan-badge').inner_text()
                assert 'not all claims resolved' in page.locator('#plan-badge').inner_text()
                assert s.candidate('redundant')['status']=='unresolved'
                checks.append('Finite equality says cost stable, without changing unknown verdicts')
                page.click('#guided-button');page.wait_for_timeout(100)
                assert s.release['cost']==3 and s.release['verified']
                assert 'Graph-guided policy' in page.locator('#agent-message').inner_text()
                checks.append('Guided control, per-split release review and verified output render')
                page.locator('#planning-section').scroll_into_view_if_needed();page.screenshot(path=str(output/'cost-stable-desktop.png'))
                page.set_viewport_size({'width':390,'height':844});page.locator('#planning-section').scroll_into_view_if_needed();page.wait_for_timeout(100)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(output/'planner-mobile.png'))
                checks.append('390px mobile view has no page-wide horizontal overflow')
                browser.close()
        finally:s.close()
    report={'test_only':True,'checks':checks,'page_errors':errors,'native_falkordb':False,'real_mcp':False,'provider_requests':0}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if errors:raise RuntimeError(errors)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
