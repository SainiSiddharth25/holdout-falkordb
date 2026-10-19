#!/usr/bin/env python3
"""Optional browser UI regression, using explicit TEST-ONLY service adapters.

Requires developer-installed Playwright and Chromium. Does NOT test FalkorDB,
MCP, or a model. There are no provider calls; screenshots are not live-run proof.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import re
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))

from holdout.ingest import prepare_rows
from stage11_support import service_modules
from playwright.sync_api import sync_playwright


def main():
    out=Path(os.environ.get('HOLDOUT_UI_QA_DIR',str(ROOT/'.runtime/ui-qa'))).resolve()
    out.mkdir(parents=True,exist_ok=True)
    errors=[];checks=[]
    with tempfile.TemporaryDirectory(prefix='holdout-ui-') as work, service_modules() as service_api:
        work=Path(work);counter=0
        active=service_api.HoldoutService(runtime_dir=work/'initial')
        def current():
            value=active.state()
            value['provider_available']=True  # Only test the button hierarchy, never invoke an LLM.
            value['engine']['name']='UI test adapter — not native validation'
            return value
        def handle(url, body=None):
            nonlocal active,counter
            path=urlparse(url).path
            payload=json.loads(body) if body else {}
            try:
                if path=='/api/state':result=current()
                elif path.startswith('/api/candidates/'):result=active.candidate_detail(path.rsplit('/',1)[1])
                elif path=='/api/import':
                    counter+=1;dest=work/f'input-{counter}'
                    prepare_rows(payload['train'],payload['evaluation'],dest,policy=payload['policy'],separation_policy=payload['separation_policy'])
                    previous=active;active=service_api.HoldoutService(dest,work/f'run-{counter}');previous.close();result=current()
                elif path=='/api/run':
                    assert payload['mode']=='deterministic','Browser test must never request an LLM.'
                    while (candidate:=active.next_candidate()) is not None:active.investigate(candidate['id'])
                    active.stage_release()
                    active.agent.update(status='completed',mode='deterministic',message='UI test driver completed service calls. No native engine, MCP, or provider was exercised.')
                    result=current()
                elif path=='/api/release':active.stage_release();result=current()
                elif path=='/api/investigate':active.investigate(payload['candidate_id'],payload.get('method','auto'));result=current()
                else:raise AssertionError('Unexpected UI endpoint '+path)
                return result
            except Exception as exc:
                errors.append(type(exc).__name__+': '+str(exc));return {'error':str(exc)}
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':1040},device_scale_factor=1)
                page.on('pageerror',lambda error:errors.append(str(error)))
                # Render local assets in-memory: no server navigation or network access.
                html=(ROOT/'dist/index.html').read_text()
                html=re.sub(r'<script[^>]*src=[^>]*></script>', '', html)
                html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*>', '', html)
                page.set_content(html)
                page.add_style_tag(content=(ROOT/'dist/styles.css').read_text())
                page.expose_function('__holdoutTestBackend',handle)
                page.add_script_tag(content="window.fetch = async (url, options = {}) => new Response(JSON.stringify(await window.__holdoutTestBackend(String(url), options.body || null)), {status: 200, headers: {'Content-Type': 'application/json'}});")
                page.add_script_tag(content=(ROOT/'dist/app.js').read_text())
                page.locator('#llm-button').wait_for(state='visible')
                assert page.locator('#llm-button').is_visible()
                assert 'primary' in page.locator('#llm-button').get_attribute('class')
                checks.append('Live-agent primary button preserved')
                page.set_input_files('#train-file',str(ROOT/'examples/stage1_1-review/train.jsonl'))
                page.set_input_files('#eval-file',str(ROOT/'examples/stage1_1-review/eval.jsonl'))
                page.select_option('#separation-policy','answer_leakage')
                page.click('#import-button');page.wait_for_timeout(200)
                page.click('#run-button');page.wait_for_timeout(200)
                # Select the identical multi-function module, now explicitly unresolved.
                page.locator('#candidate-list [data-candidate="c000001"]').click();page.wait_for_timeout(200)
                assert 'Identical captured bytes' in page.locator('#inspector').inner_text()
                assert 'No untried applicable method remains' in page.locator('#inspector').inner_text()
                assert page.locator('#investigate-selected').is_disabled()
                assert 'Verified retention by split' in page.locator('#release-review').inner_text()
                checks.extend(['Identity is separate from answer relevance','Unavailable methods explained; exhausted action disabled','Per-split review rendered'])
                page.locator('#graph-section').scroll_into_view_if_needed()
                page.screenshot(path=str(out/'answer-policy-desktop.png'))
                page.locator('#release-section').scroll_into_view_if_needed()
                page.screenshot(path=str(out/'release-desktop.png'))
                # Same IDs across runs must not show stale evidence from the old cache.
                page.select_option('#separation-policy','exact_record')
                page.click('#import-button');page.wait_for_timeout(200)
                page.click('#run-button');page.wait_for_timeout(200)
                page.locator('#candidate-list [data-candidate="c000001"]').click();page.wait_for_timeout(200)
                assert active.candidate('c000001')['status']=='confirmed'
                assert 'Exact-record separation' in page.locator('#inspector').inner_text()
                assert '3 declared cost units' in page.locator('#release-summary').inner_text()
                checks.append('New import uses exact policy and clears stale candidate details')
                # A single duplicate pair with a protected evaluation record forces an empty train pool.
                for name,rows in [('train',[{'content':'identical text'}]),('eval',[{'content':'identical text','protected':True}])]:
                    (work/f'{name}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
                    page.set_input_files(f'#{name}-file',str(work/f'{name}.jsonl'))
                page.click('#import-button');page.wait_for_timeout(200)
                assert 'train pool is empty' in page.locator('#release-review').inner_text()
                checks.append('Empty-pool warning visible before export')
                page.click('#run-button');page.wait_for_timeout(200)
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#release-section').scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
                page.screenshot(path=str(out/'empty-pool-mobile.png'))
                checks.append('Mobile review has no horizontal overflow')
                browser.close()
        finally:active.close()
    assert not errors,errors
    report={'test_kind':'browser UI with explicit in-memory/NetworkX service adapter; NOT native/MCP/model validation','checks':checks,'page_errors':errors}
    (out/'browser-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
