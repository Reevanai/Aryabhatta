# ADR 0010: SQL-first feature definitions with Valkey for online counters

Status: Proposed (Phase 1, 2026-11-10)

Baseline change: Adds Valkey to the baseline.

## Context

The baseline asks for SQL plus a lightweight feature store pattern. The scoring target is p95
under 150 ms. Windowed aggregates computed in PostgreSQL per request would consume most of it.

## Decision

A feature is a versioned definition (entity, expression or aggregate, window, owner, purpose).
Offline values are computed by SQL as of a timestamp and used for training and backtests. Online
windowed aggregates are time-bucketed counters in Valkey (BSD-3-Clause), updated by the worker
from the event log. A nightly job recomputes a sample offline and reports mismatches. Valkey holds
tokens and numbers only and is rebuildable by replay.

## Alternatives considered

- **PostgreSQL only** — simplest, but latency risk on the hot path; kept as the degraded mode.
- **Feast** — more machinery than needed and a second definition language.
- **Stream processor (Flink, etc.)** — heavy for the target customers.

## Consequences

- One extra component beyond the baseline.
- Counter updates are asynchronous, so a burst can be scored against slightly stale counts; the scorer also increments an in-request delta to narrow this.
- Online and offline parity is a monitored metric, not an assumption.
