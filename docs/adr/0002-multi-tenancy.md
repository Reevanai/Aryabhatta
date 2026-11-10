# ADR 0002: Shared PostgreSQL with forced row-level security

Status: Proposed (Phase 1, 2026-11-10)

## Context

Sentinel is installed at a single bank or hosted for several. Tenant data must never cross.
Application-level filtering alone fails the first time a query forgets a predicate.

## Decision

Every tenant-scoped table carries `tenant_id` and has a row-level security policy with
`FORCE ROW LEVEL SECURITY`. The application role owns no tables and has no `BYPASSRLS`. Each
transaction sets `app.tenant_id` (and `app.branch_ids` for branch-scoped roles) with `SET LOCAL`;
without it, no rows are visible. Migrations use a separate role. A required test per table proves
cross-tenant reads and writes return nothing. Other layers follow the table in architecture
section 9.2. A bank needing physical isolation gets its own database with identical code.

## Alternatives considered

- **Schema per tenant** — migrations multiply by tenant count and connection pooling degrades; no stronger than forced RLS.
- **Database per tenant always** — operational cost too high for a hosted offering of many small banks; still available as a deployment option.
- **Application-level filtering only** — one missed predicate is a breach.

## Consequences

- `SET LOCAL` requires transaction-scoped connection use; the data-access layer owns this and nothing else opens connections.
- Pooling must be in transaction mode.
- Background consumers must set the tenant from the event envelope before touching the database.
- Cross-tenant analytics are impossible by design; platform metrics come from telemetry, not tenant tables.
