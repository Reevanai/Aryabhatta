# Architecture decision records

All are **Proposed** until reviewed. An ADR is superseded by a new one, never edited after acceptance.

| ADR | Decision | Change to baseline stack |
|-----|----------|--------------------------|
| [0001](0001-modular-monolith.md) | Modular monolith in a monorepo, four deployables | — |
| [0002](0002-multi-tenancy.md) | Shared PostgreSQL with forced row-level security | — |
| [0003](0003-event-streaming.md) | Kafka protocol; Redpanda in development, Apache Kafka in production | Redpanda → Kafka API with Apache Kafka as the production broker. |
| [0004](0004-graph-store.md) | GraphStore port; PostgreSQL by default, Neo4j Community optional | Neo4j or Memgraph → port with PostgreSQL default; Neo4j Community as an optional adapter. |
| [0005](0005-secrets-and-keys.md) | OpenBao and SOPS; envelope encryption with per-tenant keys | Vault → OpenBao (same API). |
| [0006](0006-rule-engine.md) | Build the rule engine; rules are versioned data, not code | — |
| [0007](0007-pii-tokenisation.md) | Deterministic per-tenant tokenisation with a separate PII vault | — |
| [0008](0008-audit-log.md) | Append-only, hash-chained audit log in PostgreSQL | — |
| [0009](0009-temporal-workflows.md) | Temporal for case, report and incident workflows | — |
| [0010](0010-feature-store.md) | SQL-first feature definitions with Valkey for online counters | Adds Valkey to the baseline. |
| [0011](0011-integration-adapters.md) | Ports and adapters for every external system, with stubs | — |
| [0012](0012-human-in-the-loop.md) | Models and rules recommend; humans decide and can override | — |
| [0013](0013-transactional-outbox.md) | Publish events through a transactional outbox | — |
| [0014](0014-llm-usage.md) | Language models are optional, read-only, tenant-scoped and cited | — |
| [0015](0015-licence-policy.md) | Dependency licence policy | — |
| [0016](0016-regulatory-reference-registry.md) | Regulatory details are registry entries with a verification status | — |
