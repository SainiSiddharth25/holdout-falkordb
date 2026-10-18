# Code workshop candidate release

This is a self-authored synthetic dataset for HOLDOUT. Every code fragment, note,
catalog entry, and capture manifest was written for this project. No real
benchmark, user history, customer data, or outside source material is included.
The files are evidence examples, not measured evidence about deployed models.

The two proposed pools contain eight training records and six evaluation
records. Exclusion costs are predeclared priority units. They do not estimate
model score changes. Protected records express requirements of this proposed
release. All candidate relationships start unresolved.

`dataset.json` is the public evidence catalog. Record content lives in `records`,
source captures in `sources` and `captures`, and hash-bound capture statements in
`manifests`. Every record is also a graph entity. An outgoing `DERIVED_FROM` or
`CONTAINS` edge describes a candidate origin; `USES_SCAFFOLD` describes reusable
boilerplate. A source reference without captured bytes deliberately cannot
establish solution preservation.

Each `solution_symbols` entry identifies a separately answer-bearing function
under this fixture's source-adapter contract. A shared helper outside that list
is not automatically an answer match. Direct comparison parses source without
executing it. Structural matching is restricted to a supported single-function
Python subset and preserves operators, constants, attributes, and global names.
More complicated Python, missing captures, and nonmatching source remain
unresolved unless other evidence establishes the candidate's claim.

The lineage inspector validates only paths returned by FalkorDB. It checks
capture hashes and exact solution-byte preservation at every supplied hop; it
does not reconstruct ancestry in Python. A common source ID alone is not proof.

The release guarantee concerns the compiled candidate graph and this explicit
policy. It is not a proof that every possible data relationship was discovered,
that a model has forgotten information, or that changing a split improves its
measured score. Expected classifications and optimization assertions belong to
the test suite, outside the agent-visible evidence catalog.
