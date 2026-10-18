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
