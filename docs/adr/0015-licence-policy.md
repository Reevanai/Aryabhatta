# ADR 0015: Dependency licence policy

Status: Proposed (Phase 1, 2026-11-10)

## Context

Several candidates are copyleft, source-available or non-commercial (`research/LICENCES.md`).

## Decision

Adopt the rules in `research/LICENCES.md` section 2. CI enforces an allow-list for Python and
JavaScript dependencies and container images from Phase 2. Any exception needs a recorded
decision.

## Alternatives considered

- **Review licences manually at release time** — too late and too easy to miss.

## Consequences

- Some technically better components are excluded.
- Model weights and data sets need the same check as code.
