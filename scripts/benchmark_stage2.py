#!/usr/bin/env python3
"""Paired, no-model planning benchmark on controlled captured evidence.

Default execution requires real FalkorDB. --offline-contract explicitly uses the
TEST adapter and marks every result accordingly; never use that as native proof.
All strategies get the same primed evidence, auto investigations, batch budget,
and (where noted) finite-gap stopping rule. Reports include planning overhead.
"""
from __future__ import annotations
from contextlib import nullcontext
from pathlib import Path
import argparse
import json
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def benchmark(Service,fixture: Path, work: Path, *, prime_ids: tuple[str,...], budget: int = 1):
    results=[]
    for strategy in ('graph_guided','endpoint_with_gap_stop','catalog_with_gap_stop','endpoint_exhaustive'):
        s=Service(fixture,work/strategy)
        try:
            for cid in prime_ids:s.investigate(cid,'exact')
            before=s.bounds();base_events=len(s.store.events())
            path=[];scenario_solves=0;elapsed=time.perf_counter()
            for _ in range(budget):
                bounds=s.bounds()
                if strategy!='endpoint_exhaustive' and bounds['gap']==0 and bounds['upper']['feasible']:
                    break
                if strategy=='graph_guided':
                    p=s.plan_investigation();scenario_solves+=p['work']['scenario_solves']
                    if p['stop']['stop']:break
                    choice=p['next_candidate']
                elif strategy=='catalog_with_gap_stop':
                    options=sorted(c['id'] for c in s.store.candidates() if c['status']=='unresolved' and s.review_candidate(c)['pending_methods'])
                    choice={'id':options[0]} if options else None
                else:choice=s.next_candidate(bounds)
                if not choice:break
                path.append(choice['id']);s.investigate(choice['id'],'auto')
            receipt=s.stage_release();after=s.bounds()
            events=s.store.events()[base_events:]
            results.append({'strategy':strategy,'batch_budget':budget,'investigation_order':path,'batches_used':len(path),
                            'evidence_methods':sum(e['tool']=='investigate_candidate' for e in events),
                            'scenario_solves':scenario_solves,'wall_seconds':round(time.perf_counter()-elapsed,6),
                            'before':{'lower':before['lower']['cost'],'upper':before['upper']['cost'],'gap':before['gap']},
                            'after':{'lower':after['lower']['cost'],'upper':after['upper']['cost'],'gap':after['gap']},
                            'verified':receipt['verified'],'engine':s.engine,
                            'unresolved':sum(c['status']=='unresolved' for c in s.store.candidates())})
        finally:s.close()
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--offline-contract',action='store_true',help='Explicit TEST adapter; NOT native/MCP/model validation.')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    if args.offline_contract:
        sys.path.insert(0,str(ROOT/'tests'))
        from stage11_support import service_modules
        context=service_modules()
    else:
        import holdout.service as service
        context=nullcontext(service)
    output=Path(args.output)
    if output.exists():p.error('Choose a new report filename.')
    with context as service, tempfile.TemporaryDirectory(prefix='holdout-stage2-bench-') as work:
        work=Path(work);cases=[]
        for name,primes in [('decision-impact',('a-x','a-y','b-y')),
                            ('decision-impact-distractor',('a-x','a-y','b-y','d-w'))]:
            for budget in (1,3):
                cases.append({'fixture':name,'budget':budget,'results':benchmark(service.HoldoutService,ROOT/'fixtures'/name/'dataset.json',work/f'{name}-{budget}',prime_ids=primes,budget=budget)})
    report={'test_only':args.offline_contract,'model_calls':0,'mcp_used':False,
            'backend':'TEST-ONLY NetworkX adapter' if args.offline_contract else 'native FalkorDB',
            'scope':'Controlled sensitivity regressions, not a real-corpus quality study. Priming work is identical and excluded from the measured continuation. Scenario work is explicitly counted. No average-case performance or LLM benefit claim.',
            'cases':cases}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'report':str(output),'backend':report['backend'],'cases':len(cases)},indent=2))


if __name__=='__main__':main()
