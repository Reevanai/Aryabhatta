# ADR 0001: Modular monolith in a monorepo, four deployables

Status: Proposed (Phase 1, 2026-11-10)

## Context

Twelve capability groups must plug in over many phases. The customers are small institutions with
small operations teams, and the smallest installation runs on one or two nodes. Only the scoring
path has a hard latency target.

## Decision

One Python codebase with one module per capability group. Modules interact only through each
other's `public.py` and through events; `import-linter` enforces this in CI. The code is deployed as
four processes (`api`, `scorer`, `worker`, `ingest`) plus the Next.js console. Python packaging uses a
`uv` workspace; the console uses `pnpm`.

## Alternatives considered

- **One service per capability group** — twelve or more deployables, distributed transactions and network hops, for teams that cannot operate them. Nothing in the requirements needs it.
- **Single deployable** — the scoring path would share resources and release risk with batch and workflow code.
- **Polyrepo** — cross-module contract changes would need coordinated releases.

## Consequences

- A module can be extracted into a service later without changing callers, because callers already use its public interface or its events.
- Boundary discipline depends on the CI rule; it must not be weakened.
- All modules share one database, so schema ownership rules (ADR 0002, architecture section 4) matter.
