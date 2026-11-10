# ADR 0004: GraphStore port; PostgreSQL by default, Neo4j Community optional

Status: Proposed (Phase 1, 2026-11-10)

Baseline change: Neo4j or Memgraph → port with PostgreSQL default; Neo4j Community as an optional adapter.

## Context

The baseline asks for Neo4j or Memgraph, chosen after research. Findings (`research/RESEARCH.md`
section 5): Memgraph's licence forbids embedding or distributing it to third parties and offering
it as a service, which covers both ways Sentinel is delivered. Neo4j Community is GPLv3, has a
single database, no clustering and no role-based access control, so tenant isolation needs one
instance per tenant. FalkorDB is SSPL. Kùzu is archived.

## Decision

Define a `GraphStore` port in the `graph` module: upsert nodes and edges, k-hop neighbourhood,
bounded path and cycle search, component and community membership, and bulk export.

Default adapter: PostgreSQL. Nodes and edges are tenant-scoped tables under the same row-level
security as all other data. Bounded traversals are recursive SQL with depth and row limits.
Whole-graph analytics run as batch jobs in NetworkX over a per-tenant extract and write results
back.

Optional adapter: Neo4j Community over Bolt, one instance per tenant, deployed by the bank. Sentinel
ships no Neo4j image and writes no server-side procedures.

The suitability of the default is a measured claim: Phase 6 benchmarks traversal latency and batch
run time on synthetic graphs at stated sizes, and this ADR is revisited if they miss targets.

## Alternatives considered

- **Neo4j Community as the primary store** — adds a second system of record without tenant isolation or access control, under GPLv3.
- **Memgraph** — licence forbids the product's distribution model.
- **Apache AGE** — promising because it keeps one database and one security model; PostgreSQL 16 support and algorithm coverage were not verified. Assessed in Phase 6 as a query-layer upgrade to the default adapter.
- **Neo4j Enterprise** — commercial licence cost passed to small banks.

## Consequences

- Graph features work with no extra infrastructure in the smallest deployment.
- Very large or deep traversals will be slower than in a native graph engine; limits are explicit and tested.
- Two adapters must pass the same contract test suite.
- Community detection uses NetworkX Louvain; the maintained Leiden implementations are GPL and are not linked.
