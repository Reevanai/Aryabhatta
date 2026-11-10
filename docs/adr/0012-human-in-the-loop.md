# ADR 0012: Models and rules recommend; humans decide and can override

Status: Proposed (Phase 1, 2026-11-10)

## Context

A non-negotiable rule of the project, and the posture regulators expect. Automated freezes or
filings on a false positive harm customers.

## Decision

The scorer returns recommendations only. Every action with external effect is a workflow step
that waits for approval from a named, authorised human. Every score stores its reasons: rule hits
with triggering values, model version, top feature contributions, thresholds. Overrides require a
reason, are audited and become labels. AI-generated text is stored and shown as a draft with the
evidence it used.

## Alternatives considered

- **Auto-action above a confidence threshold** — prohibited by project rules.

## Consequences

- Throughput depends on analyst capacity; prioritisation quality matters and is measured.
- The bank's own switch may choose to act on a recommendation automatically; that is the bank's configuration and is outside Sentinel.
