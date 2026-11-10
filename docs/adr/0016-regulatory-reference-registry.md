# ADR 0016: Regulatory details are registry entries with a verification status

Status: Proposed (Phase 1, 2026-11-10)

## Context

Project rule: do not invent clause numbers, report formats or API specifications. Research also
showed the regulatory baseline changing (RBI reissued supervisory directions on 31 July 2026), and
most primary texts could not be read in Phase 0.

## Decision

`core.regulatory_reference` records each obligation: instrument, reference number, paragraph,
date, source URL, verification date, verifier, and status (`verified`, `secondary_only`,
`unverified`, `superseded`). Deadlines, thresholds, layouts and control lists are configuration that
references an entry. Features depending on an entry that is not `verified` run in draft mode with
export disabled. Superseding an entry flags dependent configuration for review.

## Alternatives considered

- **Constants in code with comments** — unreviewable and silently stale.
- **Leave everything as free-text TODOs** — no mechanism to block an unverified export.

## Consequences

- Someone must own verification; it is a named task per phase.
- The demo shows draft-mode banners until primary sources are read.
