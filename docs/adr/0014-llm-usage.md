# ADR 0014: Language models are optional, read-only, tenant-scoped and cited

Status: Proposed (Phase 1, 2026-11-10)

## Context

Case summaries and natural-language investigation are required features. Model output can be
wrong, and personal data must not leave the bank's control without agreement.

## Decision

A `TextModelProvider` port with no default vendor. The feature is off unless a tenant configures
a provider. Models receive tokens and structured facts, not clear PII. Natural-language questions
are translated to calls on a fixed set of read-only, parameterised query tools executed under the
user's own tenant and role; the model never writes SQL that is executed. Every statement in an
answer or summary links to the records it came from; text without a citation is not shown. Output is
labelled as a draft and never changes case state.

## Alternatives considered

- **Free-form text-to-SQL** — injection and data-scope risk.
- **Bundled hosted model** — data-residency and consent questions the bank must decide.

## Consequences

- Feature quality depends on the provider the bank chooses.
- A deterministic template-based summary is the fallback when no provider is configured.
