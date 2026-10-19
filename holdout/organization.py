"""Stage 5: bounded synthetic organization context and release governance.

The organization's documents, roles and decisions are queried from FalkorDB.
These are demonstrative review attestations, not authenticated signatures.
No LLM or HTTP requester can grant itself trusted authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


class OrganizationError(ValueError):
    pass


class OrganizationBrain:
    def __init__(self, store, catalog: Path, audit_path: Path, run_id: str):
        self.store = store
        self.catalog_path = catalog.resolve()
        self.root = self.catalog_path.parent
        self.data = json.loads(self.catalog_path.read_text(encoding='utf-8'))
        self._validate()
        self.catalog_fingerprint = digest(canonical(self.data))
        self.audit_path = audit_path
        self.run_id = run_id
        self._seed()
        self._restore_policy_selection()
        self._restore_attestations()

    def _validate(self):
        d = self.data
        if d.get('schema_version') != 1 or not d.get('synthetic'):
            raise OrganizationError('Stage 5 requires explicitly synthetic organization context.')
        if not isinstance(d.get('project'), dict) or not d['project'].get('id'):
            raise OrganizationError('Organization project is missing.')
        for key in ('datasets', 'people', 'decisions', 'policies'):
            if not isinstance(d.get(key), list) or not d[key]:
                raise OrganizationError('Missing organization ' + key)
            if len({row.get('id') for row in d[key]}) != len(d[key]):
                raise OrganizationError('Repeated organization identifiers.')
        people = {p['id']: p for p in d['people']}
        policies = {p['id']: p for p in d['policies']}
        if d.get('default_policy') not in policies:
            raise OrganizationError('Unknown default organization policy.')
        for p in policies.values():
            if p.get('reviewer_role') not in {person['role'] for person in people.values()}:
                raise OrganizationError('Policy references missing reviewer role.')
            for key in ('min_train_retained', 'min_eval_retained', 'auto_approve_cost'):
                if type(p.get(key)) is not int or p[key] < 0:
                    raise OrganizationError('Policy requires nonnegative integer gate thresholds.')
            if type(p.get('always_review')) is not bool:
                raise OrganizationError('Policy always_review must be boolean.')
        for ds in d['datasets']:
            if ds.get('split') not in {'train', 'eval'} or ds.get('owner_id') not in people:
                raise OrganizationError('Dataset owner or split is invalid.')
        for decision in d['decisions']:
            if decision.get('policy_id') not in policies:
                raise OrganizationError('Decision policy is invalid.')
            source = (self.root / decision.get('source_path', '')).resolve()
            if not source.is_relative_to(self.root) or not source.is_file():
                raise OrganizationError('Organizational decision source is missing or escapes root.')
            if digest(source.read_bytes()) != decision.get('source_sha256'):
                raise OrganizationError('Organizational policy source hash mismatch.')

    def _check_sources(self):
        for decision in self.data['decisions']:
            path = (self.root / decision['source_path']).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or digest(path.read_bytes()) != decision['source_sha256']:
                raise OrganizationError('Organizational source changed; refusing governance decision.')
        if digest(canonical(json.loads(self.catalog_path.read_text(encoding='utf-8')))) != self.catalog_fingerprint:
            raise OrganizationError('Organizational catalog was changed on disk.')

    def _q(self, query, values=None):
        return self.store._query(query, values or {})

    def _seed(self):
        d = self.data
        self._q('MERGE (p:OrgProject {id:$id}) SET p.data=$data', {'id':d['project']['id'], 'data':canonical(d['project']).decode()})
        self._q('UNWIND $rows AS row MERGE (p:OrgPerson {id:row.id}) SET p.data=row.data, p.role=row.role',
                {'rows':[{'id':p['id'], 'data':canonical(p).decode(), 'role':p['role']} for p in d['people']]})
        self._q('UNWIND $rows AS row MERGE (d:OrgDataset {id:row.id}) SET d.data=row.data,d.split=row.split',
                {'rows':[{'id':p['id'], 'data':canonical(p).decode(), 'split':p['split']} for p in d['datasets']]})
        self._q('UNWIND $rows AS row MATCH (d:OrgDataset {id:row.id}),(o:OrgPerson {id:row.owner}) MERGE (d)-[:OWNED_BY]->(o)',
                {'rows':[{'id':ds['id'], 'owner':ds['owner_id']} for ds in d['datasets']]})
        self._q('MATCH (p:OrgProject {id:$id}),(d:OrgDataset) MERGE (p)-[:HAS_DATASET]->(d)',{'id':d['project']['id']})
        self._q("MATCH (p:OrgProject {id:$id}),(m:ReleaseMeta {id:'release'}) MERGE (p)-[:REVIEWS_RELEASE]->(m)",{'id':d['project']['id']})
        self._q('UNWIND $rows AS row MERGE (p:OrgPolicy {id:row.id}) SET p.data=row.data',
                {'rows':[{'id':p['id'], 'data':canonical(p).decode()} for p in d['policies']]})
        self._q('UNWIND $rows AS row MERGE (d:OrgDecision {id:row.id}) SET d.data=row.data',
                {'rows':[{'id':p['id'], 'data':canonical(p).decode()} for p in d['decisions']]})
        self._q('UNWIND $rows AS row MATCH (p:OrgPolicy {id:row.policy}),(d:OrgDecision {id:row.id}) MERGE (p)-[:JUSTIFIED_BY]->(d)',
                {'rows':[{'id':x['id'], 'policy':x['policy_id']} for x in d['decisions']]})
        self._q('MATCH (project:OrgProject {id:$id}),(policy:OrgPolicy) MERGE (project)-[:GOVERNED_BY]->(policy)',{'id':d['project']['id']})
        self._q('MATCH (dataset:OrgDataset),(r:Record) WHERE dataset.split=r.split MERGE (dataset)-[:HAS_RECORD]->(r)')
        self._q('MERGE (choice:OrgPolicySelection {id:$id}) ON CREATE SET choice.policy_id=$policy',
                {'id':self.run_id, 'policy':d['default_policy']})
        self._q("MATCH (choice:OrgPolicySelection {id:$id}),(m:ReleaseMeta {id:'release'}) MERGE (m)-[:REVIEWED_UNDER]->(choice)",{'id':self.run_id})

    def policy_id(self) -> str:
        rows = self._q('MATCH (s:OrgPolicySelection {id:$id}) RETURN s.policy_id',{'id':self.run_id})
        return rows[0][0]

    def active_policy(self) -> dict:
        self._check_sources()
        rows = self._q('MATCH (s:OrgPolicySelection {id:$id}),(p:OrgPolicy) WHERE p.id=s.policy_id RETURN p.data', {'id':self.run_id})
        if len(rows) != 1:
            raise OrganizationError('No active organization policy found in FalkorDB.')
        policy = json.loads(rows[0][0])
        if policy != next((p for p in self.data['policies'] if p['id']==policy['id']), None):
            raise OrganizationError('Organization policy differs from captured source.')
        return policy

    def _restore_policy_selection(self):
        path = self.audit_path.with_name(self.audit_path.name + '.policy')
        if not path.exists():
            return
        try:
            selected = json.loads(path.read_text(encoding='utf-8'))
            if (selected.get('catalog_sha256') != self.catalog_fingerprint or
                    selected.get('run_id') != self.run_id or
                    selected.get('policy_id') not in {p['id'] for p in self.data['policies']}):
                raise ValueError('Invalid policy-selection snapshot.')
            self._q('MATCH (s:OrgPolicySelection {id:$id}) SET s.policy_id=$policy',
                    {'id':self.run_id,'policy':selected['policy_id']})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OrganizationError('Cannot safely replay organization policy selection.') from exc

    def select_policy(self, policy_id: str):
        self._check_sources()
        if policy_id not in {p['id'] for p in self.data['policies']}:
            raise OrganizationError('Policy is not an approved option in this synthetic context.')
        self._q('MATCH (s:OrgPolicySelection {id:$id}) SET s.policy_id=$policy', {'id':self.run_id, 'policy':policy_id})
        path = self.audit_path.with_name(self.audit_path.name + '.policy')
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        try:
            pending.write_bytes(canonical({'run_id':self.run_id, 'catalog_sha256':self.catalog_fingerprint,'policy_id':policy_id}))
            os.replace(pending,path)
        finally:
            pending.unlink(missing_ok=True)
        return self.active_policy()

    def owner_routes(self) -> list[dict]:
        rows = self._q('MATCH (p:OrgProject {id:$project})-[:HAS_DATASET]->(d:OrgDataset)-[:OWNED_BY]->(o:OrgPerson) RETURN d.data,o.data ORDER BY d.id',
                       {'project':self.data['project']['id']})
        return [{'dataset':json.loads(ds),'owner':json.loads(owner)} for ds,owner in rows]

    def decision_sources(self) -> list[dict]:
        policy_id = self.policy_id()
        rows = self._q('MATCH (p:OrgPolicy {id:$id})-[:JUSTIFIED_BY]->(d:OrgDecision) RETURN d.data ORDER BY d.id', {'id':policy_id})
        result = [json.loads(r[0]) for r in rows]
        for source in result:
            path = self.root / source['source_path']
            source['source_excerpt'] = path.read_text(encoding='utf-8')[:440]
        return result

    def _release_binding(self, release: dict) -> str:
        return digest(canonical({'run_id':self.run_id, 'revision':release['revision'],
                                 'cost':release['cost'], 'kept':sorted(release['kept']),
                                 'excluded':sorted(release['excluded']),
                                 'catalog':self.catalog_fingerprint}))

    def gate(self, release: dict | None, records: list[dict], candidates: list[dict]) -> dict:
        policy = self.active_policy()
        retained = set(release['kept']) if release else set()
        train = sum(r['id'] in retained and r['split']=='train' for r in records)
        evaluation = sum(r['id'] in retained and r['split']=='eval' for r in records)
        unresolved = sum(c['status']=='unresolved' for c in candidates)
        reasons = []
        if release is None or release.get('verified') is not True:
            return {'status':'not_staged','policy':policy,'reasons':['A verified repair must be staged before governance review.'],
                    'retained':{'train':train,'eval':evaluation}, 'unresolved':unresolved, 'approval':None,
                    'publishable':False, 'scope':'Synthetic local demo approval; not production identity authentication.'}
        if train < policy['min_train_retained']:
            reasons.append(f"Only {train} training records retained; policy requires {policy['min_train_retained']}.")
        if evaluation < policy['min_eval_retained']:
            reasons.append(f"Only {evaluation} evaluation records retained; policy requires {policy['min_eval_retained']}.")
        binding = self._release_binding(release)
        approvals = self._q('MATCH (a:OrgApproval {release_binding:$binding,policy_id:$policy}) RETURN a.data ORDER BY a.created_at',
                            {'binding':binding,'policy':policy['id']})
        valid_approvals = [json.loads(a[0]) for a in approvals]
        current = valid_approvals[-1] if valid_approvals else None
        needs_approval = (policy['always_review'] or unresolved > 0 or release['cost'] > policy['auto_approve_cost'])
        if reasons:
            status = 'blocked'
        elif current and current['decision'] == 'rejected':
            status = 'rejected'
        elif current and current['decision'] == 'approved':
            status = 'approved'
        elif needs_approval:
            status = 'awaiting_approval'
        else:
            status = 'eligible_without_review'
        if needs_approval and not current:
            reasons.append('Approval required from the designated evaluation owner.')
        if current and current['decision']=='rejected':
            reasons.append('The designated reviewer rejected this exact release and policy.')
        return {'status':status,'policy':policy,'reasons':reasons,
                'retained':{'train':train,'eval':evaluation},'unresolved':unresolved,
                'approval':current,'release_binding':binding,
                'publishable':status in {'approved','eligible_without_review'},
                'scope':'Local synthetic review eligibility only; not an authenticated deployment or publication.'}

    def _append_audit(self, event: dict):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        old = []
        if self.audit_path.is_file():
            old = json.loads(self.audit_path.read_text(encoding='utf-8'))
            if not isinstance(old,list):
                raise OrganizationError('Approval event log is malformed.')
        raw = canonical(old+[event])
        pending = self.audit_path.with_name(self.audit_path.name + '.' + uuid4().hex + '.tmp')
        try:
            with pending.open('wb') as fd:
                fd.write(raw)
                fd.flush(); os.fsync(fd.fileno())
            os.replace(pending,self.audit_path)
        finally:
            pending.unlink(missing_ok=True)

    def _write_approval(self, event: dict):
        self._q('MERGE (a:OrgApproval {id:$id}) SET a.data=$data,a.release_binding=$binding,a.policy_id=$policy,a.created_at=$created',
                {'id':event['id'],'data':canonical(event).decode(),'binding':event['release_binding'],
                 'policy':event['policy_id'],'created':event['created_at']})
        self._q('MATCH (a:OrgApproval {id:$id}),(o:OrgPerson {id:$actor}) MERGE (a)-[:RECORDED_AS]->(o)',
                {'id':event['id'],'actor':event['actor_id']})
        self._q('MATCH (a:OrgApproval {id:$id}),(p:OrgPolicy {id:$policy}) MERGE (a)-[:ASSESSED_UNDER]->(p)',
                {'id':event['id'],'policy':event['policy_id']})
        self._q("MATCH (a:OrgApproval {id:$id}),(m:ReleaseMeta {id:'release'}) MERGE (a)-[:ABOUT_TECHNICAL_RELEASE]->(m)",
                {'id':event['id']})

    def _restore_attestations(self):
        if not self.audit_path.is_file():
            return
        try:
            events = json.loads(self.audit_path.read_text(encoding='utf-8'))
            if not isinstance(events,list) or len(events)>500:
                raise ValueError('Invalid approval log.')
            for event in events:
                if (event.get('run_id') != self.run_id or event.get('org_catalog_sha256') != self.catalog_fingerprint or
                        event.get('actor_id') not in {p['id'] for p in self.data['people']} or
                        event.get('policy_id') not in {p['id'] for p in self.data['policies']} or
                        event.get('decision') not in {'approved','rejected'} or not event.get('release_binding')):
                    raise ValueError('Invalid approval entry.')
                self._write_approval(event)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            raise OrganizationError('Cannot safely replay the local approval log.') from exc

    def review(self, actor_id: str, decision: str, release: dict | None, records: list[dict], candidates: list[dict]) -> dict:
        if decision not in {'approved','rejected'}:
            raise OrganizationError('Decision must be approved or rejected.')
        gate = self.gate(release,records,candidates)
        if gate['status'] in {'not_staged','blocked'}:
            raise OrganizationError('A technically verified, policy-compliant release must be staged first.')
        reviewer = next((p for p in self.data['people'] if p['id']==actor_id), None)
        policy = gate['policy']
        if not reviewer or reviewer['role'] != policy['reviewer_role']:
            raise OrganizationError('Selected reviewer does not have the policy-required role.')
        # A person must also be connected to the appropriate evaluation dataset.
        if not any(route['dataset']['split']=='eval' and route['owner']['id']==actor_id for route in self.owner_routes()):
            raise OrganizationError('Reviewer must own the affected evaluation dataset in the graph.')
        event = {'id':uuid4().hex, 'run_id':self.run_id, 'actor_id':actor_id,
                 'decision':decision,'policy_id':policy['id'], 'release_binding':gate['release_binding'],
                 'org_catalog_sha256':self.catalog_fingerprint,
                 'created_at':datetime.now(timezone.utc).isoformat()}
        self._append_audit(event)
        self._write_approval(event)
        return self.gate(release,records,candidates)

    def overview(self, release: dict | None, records: list[dict], candidates: list[dict]) -> dict:
        return {'project':deepcopy(self.data['project']),
                'datasets_and_owners':self.owner_routes(),
                'policy_options':[{'id':p['id'],'title':p['title']} for p in self.data['policies']],
                'decision_sources':self.decision_sources(),
                'people':[{'id':p['id'],'display_name':p['display_name'],'role':p['role']} for p in self.data['people']],
                'gate':self.gate(release,records,candidates),
                'scope':'Synthetic example organization; approvals are untrusted local demo attestations without login, signatures or remote publication.'}
