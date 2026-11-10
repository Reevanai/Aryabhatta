# ADR 0009: Temporal for case, report and incident workflows

Status: Proposed (Phase 1, 2026-11-10)

## Context

Cases, STR/CTR preparation and incident reporting are long-running, have deadlines, wait for
human approvals and must survive restarts.

## Decision

Model each as a Temporal workflow. Human approvals are signals. Regulatory clocks are durable
timers that raise escalations before the deadline. Activities set tenant context and are
idempotent. Workflow state is not the system of record: every state change is also written to the
module's tables and the audit log. One namespace per environment, with tenant id in workflow ids
and as a search attribute.

## Alternatives considered

- **State machine in the database plus a scheduler** — re-implements timers, retries and visibility.
- **Celery or similar task queue** — no durable long-running state.
- **Namespace per tenant** — revisit for hosted deployments with many tenants if isolation or quota needs arise.

## Consequences

- Temporal is another service to run; it uses PostgreSQL for persistence to avoid a second database.
- Workflow code must be deterministic and versioned carefully.
