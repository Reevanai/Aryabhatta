# ADR 0013: Publish events through a transactional outbox

Status: Proposed (Phase 1, 2026-11-10)

## Context

State changes and their events must not diverge. Writing to the database and the broker in one
request is not atomic.

## Decision

Each state change inserts its event into `core.outbox` in the same transaction. A relay publishes
rows in order per key and marks them sent. Consumers are idempotent on `event_id`.

## Alternatives considered

- **Dual write** — loses or duplicates events on failure.
- **Change data capture** — adds a connector platform and couples events to table shapes.

## Consequences

- Events are delayed by the relay interval; outbox delay is a monitored indicator.
- The outbox table needs pruning.
