# ADR 0003: Kafka protocol; Redpanda in development, Apache Kafka in production

Status: Proposed (Phase 1, 2026-11-10)

Baseline change: Redpanda → Kafka API with Apache Kafka as the production broker.

## Context

The baseline names Redpanda. Its core is under the Business Source Licence 1.1 (licence file read
2026-11-10). Sentinel's use is permitted, but project policy is not to ship source-available
software to customers (`research/LICENCES.md`).

## Decision

Code only against the Kafka protocol using a standard client. Development compose uses Redpanda
because it is a single container. Helm charts default to Apache Kafka (Apache-2.0). No
Redpanda-specific API or enterprise feature is used. Integration tests run against Redpanda;
a nightly job runs the same suite against Apache Kafka.

## Alternatives considered

- **Redpanda everywhere** — would ship BSL software to banks and into a hosted service.
- **Apache Kafka everywhere** — heavier development environment; acceptable fallback if the nightly parity job proves troublesome.
- **PostgreSQL as the queue** — adequate at small scale but gives no replayable log for rebuilding counters and graph state.
- **NATS** — no advantage over the Kafka ecosystem for replay and consumer groups here.

## Consequences

- Two brokers must stay behaviour-compatible for the features used; the nightly job detects drift.
- A bank that already runs Kafka can point Sentinel at it.
