# HOLDOUT Sentinel Stage 5 — Company Brain

## The point of this stage

A technically optimal release is not automatically an organizationally acceptable release. Stage 5 connects **synthetic** project, dataset, owner, policy, historical policy-decision and review attestation nodes in FalkorDB. It uses these graph relationships to answer *who owns this benchmark*, *which policy governs it*, *what decision/document justifies that policy*, and *is this verified repair eligible for review?*

This is not generic text retrieval: changing the connected policy changes actual release eligibility. It cannot change the mathematical certificate or evidence verdict.

## Data model

- `OrgProject` — synthetic project and research mission
- `OrgDataset` — training and evaluation sets, `HAS_RECORD` edges to the technical `Record` nodes
- `OrgPerson` — synthetic owners with role, `OrgDataset-[:OWNED_BY]->OrgPerson`
- `OrgPolicy` — named, validated choice with minimum per-split counts and review threshold
- `OrgDecision` — synthetic, hash-bound Markdown source justifying policy; `OrgPolicy-[:JUSTIFIED_BY]->OrgDecision`
- `OrgPolicySelection` — selected rule per run; `ReleaseMeta-[:REVIEWED_UNDER]->OrgPolicySelection`
- `OrgApproval` — local demo reviewer record linked via `RECORDED_AS`, `ASSESSED_UNDER`, and `ABOUT_TECHNICAL_RELEASE`

`OrgProject-[:REVIEWS_RELEASE]->ReleaseMeta` connects governance to the existing technical release. All organization node creation and lookups are parameterized Cypher queries; the source policy catalog is `fixtures/organization/organization.json`, with SHA-256-bound synthetic Markdown decisions in `fixtures/organization/policies`.

## Decision logic

The selected policy is read from FalkorDB, and the eligible release is the **staged, independently verified conservative repair**. Evaluation compares the real retained train/eval counts and unresolved candidate count:

1. No verified release → `not_staged`.
2. Minimum split retention violated → `blocked`, regardless of local reviewer claim.
3. A reviewer has explicitly rejected this release and policy → `rejected`.
4. A designated synthetic evaluation owner approved this release and policy → `approved`.
5. A review is required but not supplied → `awaiting_approval`.
6. Otherwise → `eligible_without_review` (only permitted under the selected synthetic policy).

Available demonstration policies:

| Rule | Min train | Min eval | Required reviewer | Behavior |
| --- | ---: | ---: | --- | --- |
| `research_review` | 1 | 1 | evaluation_owner | Requires review on unresolved risk or nonzero cost |
| `benchmark_strict` | 1 | 2 | evaluation_owner | Always requires review; blocks undersized evaluation pool |
| `sandbox` | 0 | 0 | evaluation_owner | For local experiments only; unresolved evidence still requires review |

**Controlled demonstration** — `fixtures/decision-impact` with graph-guided investigation produces a verified repair of cost 3, retaining 2 training and 1 evaluation example. Research review is pending; benchmark strict blocks; synthetic Mira's local demo approval marks research review approved; switching to benchmark strict blocks again, without touching the technical repair.

## API and agent boundaries

- `GET /api/organization` — current graph-grounded organization overview
- `POST /api/organization/policy` — choose among explicitly captured policy IDs
- `POST /api/organization/review` — local *unauthenticated* reviewer event; accepted only for a staged verified, policy-compliant release with a configured evaluation owner
- MCP `organization_context` — read-only ownership, policy and gate. The LLM cannot approve a release.

Current organization changes use the same origin-restricted local UI as the project. They **do not provide real user authentication, authorization, signed attestations, or a production approval workflow**. Review action logs are local files replayed on restart, checked for structural and release binding, but **not cryptographically authenticated**; an operator with filesystem access can rewrite them. Do not expose this service as a public multiuser application.

## Recovery and version binding

The selected policy and demo reviewer actions are stored under the existing persistent runtime volume. After a service restart, captured source evidence is independently replayed and a staged repair is reverified, then governance records are reconstructed. Prior approval only applies to the same run, revision, retained/excluded records and organization catalog under the same selected policy. New releases and changed policies cannot reuse an unrelated approval.

## Verification limits and future work

This version intentionally uses synthetic project and policy sources. It does not ingest actual company tickets, calendars, private documents, or identity-provider memberships. Real Track 3 readiness requires authenticated identities, role/attribute-based access control, organizational source permissions, multi-tenant isolation, signed or tamper-evident decisions, and a reliable integration with CI approvals.

### Native acceptance checklist

1. Real FalkorDB nodes/relationships match synthetic source catalog.
2. MCP `organization_context` returns the source-backed owner path.
3. Verified cost-3 release gives `awaiting_approval` under `research_review`.
4. Changing to `benchmark_strict` gives `blocked`, with the reason `min eval=2`.
5. Local review by unrelated training owner fails, and evaluation owner succeeds.
6. Review status survives a valid checkpoint restart but does not transfer to a new run.
7. Original release hashes and min-cut certificate remain unchanged by governance selection.
8. Hostile or modified organization source documents fail closed.

Execute native tests using `tests/test_stage5_native.py`; this environment has not executed them.
