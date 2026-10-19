// Representative queries used by holdout/graph.py and optimizer.py.
// User-provided strings are parameters; relation types come from an allowlist.

// Read the authoritative exact record snapshots.
MATCH (r:Record)
RETURN r.record_data
ORDER BY r.id;

// Bounded provenance, with all evidence metadata retained on nodes and links.
MATCH p=(r:Entity {id:$id})-[:DERIVED_FROM|CONTAINS|USES_SCAFFOLD*0..8]->(n)
RETURN [x IN nodes(p) | x.data], [x IN relationships(p) | x.data]
LIMIT 5001;

// Revision-checked candidate update.
MATCH (c:Candidate {id:$id}), (m:ReleaseMeta {id:'release'})
WHERE $expected < 0 OR m.revision=$expected
SET c.status=$status, c.data=$data, m.revision=m.revision+1
RETURN m.revision;

// In a fresh UUID-scoped flow graph, after uploading the capacity network:
MATCH (s:Flow {key:'__source'}), (t:Flow {key:'__sink'})
CALL algo.maxFlow({
  sourceNodes:[s],
  targetNodes:[t],
  relationshipTypes:['CAPACITY'],
  nodeLabels:['Flow'],
  capacityProperty:'capacity'
})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow, [e IN edges | id(e)], edgeFlows;

// Read ALL capacities to reconstruct the residual graph, including zero flows.
MATCH (s)-[e:CAPACITY]->(t)
RETURN id(e), s.key, t.key, e.capacity;

// Stage 1.1: the immutable import selects the conflict rule.
// It is stored as metadata, never edited by a model tool.
MATCH (m:ReleaseMeta {id:'release'})
RETURN m.fixture_id, m.revision, m.separation_policy;
// record_identity verifies local captured bytes; it is not a Cypher similarity
// query. Exact-record policy must not be described as an answer-leakage proof.

// Stage 2: counterfactuals do not UPDATE Candidate statuses in the evidence graph.
// The Python planner reads a revision-bound snapshot. It compiles a separate
// UUID scratch Flow graph for one assumed outcome, using the same queries above.
// Rejection: conservative set minus this one claim. Confirmation: confirmed set
// plus this one claim. Duplicate hypotheses on the same pair remain respected.
// This is repeated native max-flow, NOT a claimed incremental flow algorithm.
// Planner events are inspectable in the active evidence graph:
MATCH (e:Event) RETURN e.data ORDER BY e.at, e.id;

// === Sentinel Stage 3: evidence-bound agent memory ===
// These queries describe *implemented* FalkorDB memory links.
// Actual statements parameterize every user-supplied value (id, type, receipt).
// Evidence is assessed in Python first, never assigned through model-generated Cypher.

// Service upsert, with UNWIND $rows of validated receipt-derived documents:
UNWIND $rows AS row
MATCH (c:Candidate {id:row.candidate_id})
MERGE (r:EvidenceReceipt {id:row.receipt_id})
MERGE (m:Memory {id:row.id})
ON CREATE SET m.kind=row.kind, m.data=row.data
MERGE (m)-[:ABOUT]->(c)
MERGE (m)-[:SUPPORTED_BY]->(r);

// Current/historical record retrieval; current validity is established
// *after* the query by replayable source/policy/receipt revalidation.
MATCH (m:Memory)-[:ABOUT]->(c:Candidate)
WHERE ($candidate IS NULL OR c.id=$candidate)
  AND ($kind IS NULL OR m.kind=$kind)
RETURN m.data ORDER BY c.id,m.kind,m.id LIMIT 500;

// Explain what this agent memory is linked to:
MATCH (m:Memory)-[:ABOUT]->(c:Candidate),
      (m)-[:SUPPORTED_BY]->(e:EvidenceReceipt)
RETURN m.id, m.kind, c.id, e.id LIMIT 50;

// === Sentinel Stage 4: cooperative specialist tasks ===
// Create one task per candidate; handoffs never make evidence conclusions.
MATCH (c:Candidate)
MERGE (t:InvestigationTask {id:c.id})
ON CREATE SET t.state=CASE WHEN c.status='unresolved' THEN 'pending' ELSE 'done' END,
  t.role='code',t.owner='',t.token='',t.lease_until=0,t.attempts=0,t.handoffs=0
MERGE (t)-[:FOR_CANDIDATE]->(c);

// Claim is a single conditional query with a fresh fencing token.
MATCH (t:InvestigationTask)-[:FOR_CANDIDATE]->(c:Candidate)
WHERE t.role=$role AND c.status='unresolved' AND
 (t.state='pending' OR (t.state='leased' AND t.lease_until < $now))
WITH t ORDER BY t.id LIMIT 1
SET t.state='leased',t.owner=$owner,t.token=$token,
 t.lease_until=$until,t.attempts=t.attempts+1
RETURN t.id,t.role,t.attempts;

// Finishing / handing off requires valid current ownership. $role may be NULL.
MATCH (t:InvestigationTask {id:$id})
WHERE t.state='leased' AND t.token=$token AND t.lease_until >= $now
SET t.state=$state,t.role=coalesce($role,t.role),t.owner='',t.token='',
 t.lease_until=0,t.handoffs=t.handoffs+$handoff
RETURN t.id,t.state,t.role,t.handoffs;

// Stage 5 Company Brain: source-backed ownership and policy paths.
MATCH (project:OrgProject)-[:HAS_DATASET]->(dataset:OrgDataset)-[:OWNED_BY]->(owner:OrgPerson)
RETURN project.id,dataset.id,dataset.split,owner.id,owner.role;

// Find the actual governing decision and captured policy source for an active run.
MATCH (selection:OrgPolicySelection {id:$run_id}),(policy:OrgPolicy)
WHERE policy.id=selection.policy_id
MATCH (policy)-[:JUSTIFIED_BY]->(decision:OrgDecision)
RETURN policy.data,decision.data;

// Trace a synthetic reviewer attestation to role, policy and technical release.
MATCH (a:OrgApproval)-[:RECORDED_AS]->(owner:OrgPerson),
      (a)-[:ASSESSED_UNDER]->(policy:OrgPolicy),
      (a)-[:ABOUT_TECHNICAL_RELEASE]->(release:ReleaseMeta)
RETURN a.id,owner.id,policy.id,release.revision;
