# ADR 0006: Build the rule engine; rules are versioned data, not code

Status: Proposed (Phase 1, 2026-11-10)

## Context

Compliance staff must configure rules without developers, test them before activation, and
explain every hit. Researched engines are unsuitable: Marble (Elastic 2.0), Jube (AGPL), Tazama
(separate Node.js and NATS stack).

## Decision

Build the engine in `txn_fraud`. A rule is a JSON expression tree over a typed vocabulary:
transaction fields, entity attributes, feature references, list lookups, comparison, boolean and
arithmetic operators. There is no general-purpose code in a rule. Each rule version is immutable; a
ruleset version pins a set of rule versions and a threshold profile. Lifecycle: draft → dry-run
(evaluated live, hits recorded, no alerts) → backtest (replayed over history with precision and
recall against labels) → approval by a second person → active. The evaluator returns, for each hit,
the sub-expressions and values that made it true. Whether the evaluator is an in-house interpreter
or GoRules Zen (MIT) is decided by a latency and explainability spike in Phase 4.

## Alternatives considered

- **Embed Python or a scripting language in rules** — arbitrary code execution inside the scoring path.
- **Drools or another general BRMS** — JVM in the latency path and a rule language compliance staff will not write.
- **Adopt Tazama** — duplicates the bus, the language runtime and the tenancy model.

## Consequences

- The rule vocabulary must be extended deliberately when new signals appear.
- Backtests reuse the same evaluator and the same feature definitions as live scoring, so results are comparable.
- Typologies in the AML module are rule sets on this engine, not a second engine.
