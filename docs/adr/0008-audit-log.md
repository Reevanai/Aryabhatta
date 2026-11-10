# ADR 0008: Append-only, hash-chained audit log in PostgreSQL

Status: Proposed (Phase 1, 2026-11-10)

## Context

Investigations, regulatory reports and model decisions must be reconstructable, and tampering
must be detectable.

## Decision

A single `core.audit_event` table. The application role may only insert and select. Each record
contains the hash of the previous record in the same tenant's chain together with its own canonical
content hash. Chain heads are periodically signed and written outside the database. A verification
endpoint and a scheduled job recompute the chain. Records hold tokens and identifiers, never clear
PII.

## Alternatives considered

- **Database audit extension only** — captures statements, not business meaning, and is not tamper-evident.
- **External ledger or blockchain** — operational weight with no benefit over anchored hash chains for a single writer.
- **Log files** — no integrity, no query.

## Consequences

- Per-tenant chain writes are serialised; acceptable at expected volumes, measured in Phase 2.
- Retention of audit records is governed separately from the data they describe.
