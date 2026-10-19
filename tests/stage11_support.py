"""TEST ONLY: service contracts without an installed native engine.

The product never imports this module. The flow adapter here uses NetworkX so
these tests are NOT evidence of native FalkorDB or real MCP execution.
"""
from contextlib import contextmanager
from copy import deepcopy
import importlib
import importlib.util
import sys
import time
from uuid import uuid4
from types import ModuleType, SimpleNamespace

import networkx as nx


class TestGraphError(RuntimeError):
    pass


class TestRevisionConflict(TestGraphError):
    pass


class FlowAdapter:
    def __init__(self):
        self.nodes = []
        self.arcs = []

    def query(self, cypher, params=None):
        params = params or {}
        if cypher.startswith('UNWIND $nodes'):
            self.nodes = params['nodes']; rows = []
        elif cypher.startswith('UNWIND $arcs'):
            self.arcs = [{**a,'id':i} for i,a in enumerate(params['arcs'])]; rows=[]
        elif 'RETURN id(e)' in cypher:
            rows = [[e['id'],e['source'],e['target'],e['capacity']] for e in self.arcs]
        elif 'CALL algo.maxFlow' in cypher:
            graph = nx.DiGraph()
            graph.add_nodes_from(n['key'] for n in self.nodes)
            for edge in self.arcs:
                graph.add_edge(edge['source'],edge['target'],capacity=edge['capacity'])
            value, flows = nx.maximum_flow(graph,'__source','__sink')
            used = [e for e in self.arcs if flows[e['source']][e['target']] > 0]
            rows = [[value,[e['id'] for e in used],[flows[e['source']][e['target']] for e in used]]]
        else:
            raise AssertionError('Unexpected query in test-only flow adapter: '+cypher)
        return SimpleNamespace(result_set=rows)

    def delete(self):
        pass


class StoreAdapter:
    def __init__(self, *args, **kwargs):
        self.db = SimpleNamespace(select_graph=lambda name: FlowAdapter())
        self._events=[]; self._memories={}; self._tasks={}; self.rev=0; self.closed=False
        self._org_people = {}; self._org_datasets = {}; self._org_policies = {}
        self._org_decisions = {}; self._org_approvals = {}; self._org_choice = None

    def _query(self, cypher, params=None):
        """Test-only graph adapter for Stage 5; production uses FalkorDB Cypher."""
        import json
        params = params or {}
        if 'UNWIND $rows AS row MERGE (p:OrgPerson' in cypher:
            self._org_people = {r['id']:json.loads(r['data']) for r in params['rows']}
        elif 'UNWIND $rows AS row MERGE (d:OrgDataset' in cypher:
            self._org_datasets = {r['id']:json.loads(r['data']) for r in params['rows']}
        elif 'UNWIND $rows AS row MERGE (p:OrgPolicy' in cypher:
            self._org_policies = {r['id']:json.loads(r['data']) for r in params['rows']}
        elif 'UNWIND $rows AS row MERGE (d:OrgDecision' in cypher:
            self._org_decisions = {r['id']:json.loads(r['data']) for r in params['rows']}
        elif 'ON CREATE SET choice.policy_id=$policy' in cypher:
            if self._org_choice is None:self._org_choice = params['policy']
        elif 'SET s.policy_id=$policy' in cypher:
            self._org_choice = params['policy']
        elif 'RETURN s.policy_id' in cypher:
            return [[self._org_choice]]
        elif 'WHERE p.id=s.policy_id RETURN p.data' in cypher:
            return [[json.dumps(self._org_policies[self._org_choice])]]
        elif 'RETURN d.data,o.data' in cypher:
            return [[json.dumps(d),json.dumps(self._org_people[d['owner_id']])] for d in sorted(self._org_datasets.values(),key=lambda x:x['id'])]
        elif 'RETURN d.data ORDER BY d.id' in cypher:
            return [[json.dumps(d)] for d in sorted(self._org_decisions.values(),key=lambda x:x['id']) if d['policy_id']==params['id']]
        elif 'MERGE (a:OrgApproval' in cypher:
            event=json.loads(params['data']);self._org_approvals[event['id']]=event
        elif 'RETURN a.data ORDER BY a.created_at' in cypher:
            return [[json.dumps(a)] for a in sorted(self._org_approvals.values(),key=lambda x:x['created_at']) if a['release_binding']==params['binding'] and a['policy_id']==params['policy']]
        elif 'Org' not in cypher and 'Organization' not in cypher:
            raise AssertionError('Unrecognized test-only graph query: ' + cypher)
        return []

    def seed(self, fixture):
        self.fixture=deepcopy(fixture)
        self._records=deepcopy(fixture['records']); self._candidates=deepcopy(fixture['candidates']);self.rev=1

    def initialize_tasks(self):
        self._tasks={c['id']:{'id':c['id'],'state':'pending' if c['status']=='unresolved' else 'done',
             'role':'code','owner':None,'token':None,'lease_until_ms':0,'attempts':0,
             'handoffs':0,'verdict':c['status']} for c in self._candidates}

    def tasks(self):
        for cid,t in self._tasks.items():
            t['verdict']=next(c['status'] for c in self._candidates if c['id']==cid)
        return deepcopy([{k:v for k,v in self._tasks[cid].items() if k != "token"} for cid in sorted(self._tasks)])

    def task(self, cid):
        return next(t for t in self.tasks() if t['id']==cid)

    def claim_task(self, role, owner, lease_seconds=60):
        import re
        if role not in {'code','provenance'} or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',owner):
            raise ValueError('Invalid owner or role.')
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 300:
            raise ValueError('Invalid lease.')
        now=int(time.time()*1000)
        for t in self.tasks():
            if (t['role']==role and t['verdict']=='unresolved' and
                (t['state']=='pending' or (t['state']=='leased' and t['lease_until_ms']<now))):
                node=self._tasks[t['id']]
                token=uuid4().hex
                node.update(state='leased',owner=owner,token=token,
                            lease_until_ms=now+1000*lease_seconds,attempts=node['attempts']+1)
                return {'candidate_id':t['id'],'role':role,'owner':owner,
                        'attempt':node['attempts'],'lease_token':token,
                        'lease_until_ms':node['lease_until_ms']}
        return None

    def require_claim(self,cid,token):
        t=self._tasks[cid];now=int(time.time()*1000)
        if t['state']=='leased' and t['lease_until_ms']>=now:
            if not token or token!=t['token']:
                raise TestRevisionConflict('Task is claimed by another worker')
        elif token:
            raise TestRevisionConflict('Task token expired or revoked')

    def transition_task(self,cid,token,*,handoff_role=None):
        self.require_claim(cid,token)
        t=self._tasks[cid]
        if t['state']!='leased' or not token or t['token']!=token:
            raise TestRevisionConflict('Task claim invalid')
        if handoff_role:
            t['handoffs']+=1
        t.update(state='pending' if handoff_role else 'done',
                 role=handoff_role or t['role'],owner=None,token=None,lease_until_ms=0)
        return {'candidate_id':cid,'state':t['state'],'role':t['role'],'handoffs':t['handoffs']}

    def reconcile_tasks(self,decisions,candidate_id=None):
        for cid,choice in decisions.items():
            if candidate_id is not None and cid!=candidate_id:continue
            self._tasks[cid].update(state='done' if choice['done'] else 'pending',
                                    role=choice['role'],owner=None,token=None,lease_until_ms=0)

    def records(self): return deepcopy(sorted(self._records, key=lambda r:r["id"]))
    def candidates(self): return deepcopy(sorted(self._candidates, key=lambda c:c["id"]))
    def revision(self): return self.rev
    def events(self): return deepcopy(self._events)
    def add_event(self, event): self._events.append(deepcopy(event))
    def upsert_memories(self, items):
        for item in items:
            self._memories[item['id']] = deepcopy(item)

    def memories(self, candidate_id=None, kind=None, limit=200):
        values=sorted(self._memories.values(),key=lambda m:(m['candidate_id'],m['kind'],m['id']))
        return deepcopy([m for m in values if (candidate_id is None or m['candidate_id']==candidate_id)
                         and (kind is None or m['kind']==kind)][:limit])

    def capabilities(self):
        return {'native_maxflow':True,'module_version_text':'TEST-ONLY-NetworkX-adapter','test_double':True}
    def close(self, **kwargs): self.closed=True

    def set_candidate(self, cid, status, evidence, expected_revision=None):
        if expected_revision is not None and expected_revision!=self.rev:
            raise TestRevisionConflict('Evidence changed')
        c=next(c for c in self._candidates if c['id']==cid)
        c.update(status=status,evidence=deepcopy(evidence));self.rev+=1
        return self.rev

    def provenance(self, rid, max_depth=8):
        # Only the test adapter discovers paths in Python. Production uses Cypher.
        nodes={r['id']:r for r in self.fixture['entities']+self.fixture['records']}
        links=self.fixture['links']; paths=[]; selected={rid}; used=[]
        def visit(ids,rels):
            paths.append({'nodes':ids,'relations':rels})
            if len(rels)>=max_depth:return
            for edge in links:
                if edge['source']==ids[-1] and edge['target'] not in ids:
                    selected.add(edge['target']);used.append(edge)
                    visit(ids+[edge['target']],rels+[edge['relation']])
        visit([rid],[])
        return {'record_id':rid,'revision':self.rev,'paths':paths,
                'nodes':[nodes[i] for i in sorted(selected)],'links':deepcopy(used)}


@contextmanager
def service_modules():
    """Scope test adapters; never pollute a native test module or installed SDK."""
    names=['holdout.graph','holdout.optimizer','holdout.planning','holdout.service']
    saved={name:sys.modules.get(name) for name in names}
    import holdout
    saved_attrs={name.split('.')[-1]:getattr(holdout,name.split('.')[-1],None) for name in names}
    fake=ModuleType('holdout.graph')
    fake.GraphError=TestGraphError;fake.RevisionConflict=TestRevisionConflict;fake.GraphStore=StoreAdapter
    for name in names:sys.modules.pop(name,None)
    sys.modules['holdout.graph']=fake
    try:
        service=importlib.import_module('holdout.service')
        yield service
    finally:
        for name,original in saved.items():
            sys.modules.pop(name,None)
            if original is not None:sys.modules[name]=original
        for name,value in saved_attrs.items():
            if value is None:
                if hasattr(holdout,name):delattr(holdout,name)
            else:setattr(holdout,name,value)
