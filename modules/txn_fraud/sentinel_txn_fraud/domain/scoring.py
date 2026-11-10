"""Turning rule hits into a score, reasons and a recommendation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sentinel_txn_fraud.domain.expr import Vocabulary, evaluate, validate
from sentinel_txn_fraud.domain.features import (
    DEFAULT_PARAMS,
    FEATURES,
    FIELDS,
    PARAMS,
    AccountProfile,
    Snapshot,
    TxnView,
    compute,
    fields_of,
)

VOCABULARY = Vocabulary(
    fields={name: spec[0] for name, spec in FIELDS.items()},
    features={name: spec[0] for name, spec in FEATURES.items()},
    params={name: spec[0] for name, spec in PARAMS.items()},
)
SEVERITIES = ("low", "medium", "high", "critical")


@dataclass(frozen=True)
class RuleSpec:
    """One immutable rule version, as the scorer sees it."""

    code: str
    name: str
    expression: Any
    severity: str = "medium"
    weight: float = 0.5
    channels: tuple[str, ...] = ()
    dry_run: bool = False
    version_id: str | None = None

    def check(self) -> None:
        validate(self.expression, VOCABULARY)
        if not 0 < self.weight <= 1:
            raise ValueError("weight must be greater than 0 and at most 1")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")


@dataclass(frozen=True)
class Thresholds:
    """Score at or above which a transaction is recommended for review or for holding."""

    review: float = 0.4
    hold: float = 0.75
    params: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_PARAMS))


@dataclass
class Reason:
    kind: str
    code: str
    contribution: float
    observed: dict[str, Any]
    rule_version_id: str | None = None
    dry_run: bool = False


@dataclass
class ScoreOutcome:
    score: float
    recommendation: str
    reasons: list[Reason]

    @property
    def fired(self) -> list[str]:
        return [r.code for r in self.reasons if not r.dry_run]


def _clean(value: Any) -> Any:
    return round(value, 4) if isinstance(value, float) else value


def score(
    txn: TxnView,
    snap: Snapshot,
    profile: AccountProfile,
    rules: Sequence[RuleSpec],
    thresholds: Thresholds,
) -> ScoreOutcome:
    """Evaluate every rule and combine the hits.

    Each hit contributes its weight; hits combine as 1 - product(1 - weight), so several
    moderate signals add up without any single rule needing to be certain. Dry-run rules are
    evaluated and reported but contribute nothing.
    """
    params = dict(thresholds.params)
    fields = fields_of(txn)
    features = compute(txn, snap, profile, params)
    reasons: list[Reason] = []
    clear = 1.0
    for rule in rules:
        if rule.channels and txn.channel not in rule.channels:
            continue
        result = evaluate(rule.expression, fields, features, params)
        if not result.hit:
            continue
        observed = {name: _clean(value) for name, value in result.observed.items()}
        contribution = 0.0 if rule.dry_run else rule.weight
        reasons.append(
            Reason("rule", rule.code, contribution, observed, rule.version_id, rule.dry_run)
        )
        clear *= 1 - contribution
    total = round(1 - clear, 4)
    if total >= thresholds.hold:
        recommendation = "hold_for_review"
    elif total >= thresholds.review:
        recommendation = "review"
    else:
        recommendation = "allow"
    return ScoreOutcome(total, recommendation, reasons)
