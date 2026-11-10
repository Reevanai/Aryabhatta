"""Replay history through the rules in memory: the engine behind backtests and evaluation.

Uses the same feature and scoring functions as live scoring, so a backtest result is what the
rule would have done.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sentinel_txn_fraud.domain.features import AccountProfile, TxnView
from sentinel_txn_fraud.domain.scoring import RuleSpec, ScoreOutcome, Thresholds, score
from sentinel_txn_fraud.domain.state import MemoryState

_DEFAULT_PROFILE = AccountProfile()


@dataclass
class Metrics:
    evaluated: int = 0
    flagged: int = 0
    labelled: int = 0
    true_positives: int = 0

    @property
    def false_positives(self) -> int:
        return self.flagged - self.true_positives

    @property
    def false_negatives(self) -> int:
        return self.labelled - self.true_positives

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.flagged if self.flagged else None

    @property
    def recall(self) -> float | None:
        return self.true_positives / self.labelled if self.labelled else None


@dataclass
class ReplayResult:
    overall: Metrics = field(default_factory=Metrics)
    # rule code -> hits and how many were labelled fraud
    by_rule: dict[str, Metrics] = field(default_factory=dict)
    # label group (for example a scenario type) -> recall
    by_group: dict[str, Metrics] = field(default_factory=dict)
    flagged_refs: set[str] = field(default_factory=set)
    days: float = 0.0

    @property
    def alerts_per_day(self) -> float | None:
        return self.overall.flagged / self.days if self.days else None


def replay(
    txns: Iterable[TxnView],
    rules: Sequence[RuleSpec],
    thresholds: Thresholds,
    profiles: Mapping[str, AccountProfile],
    *,
    fraud: Mapping[str, str] | None = None,
    tenant: str = "replay",
    on_score: Callable[[TxnView, ScoreOutcome], None] | None = None,
) -> ReplayResult:
    """Score transactions in time order. `fraud` maps a reference to its label group.

    A transaction is flagged when its score reaches the review threshold.
    """
    state = MemoryState()
    result = ReplayResult(by_rule={rule.code: Metrics() for rule in rules})
    fraud = fraud or {}
    groups: dict[str, Metrics] = defaultdict(Metrics)
    for label_group in fraud.values():
        groups[label_group].labelled += 1
    result.overall.labelled = len(fraud)
    first = last = None
    for txn in txns:
        first = first or txn.ts
        last = txn.ts
        snap = state.snapshot_sync(tenant, txn)
        outcome = score(txn, snap, profiles.get(txn.account, _DEFAULT_PROFILE), rules, thresholds)
        state.apply_sync(tenant, txn)
        result.overall.evaluated += 1
        group = fraud.get(txn.reference)
        for code in outcome.fired:
            metrics = result.by_rule[code]
            metrics.flagged += 1
            metrics.true_positives += int(group is not None)
        if outcome.recommendation != "allow":
            result.overall.flagged += 1
            result.flagged_refs.add(txn.reference)
            if group is not None:
                result.overall.true_positives += 1
                groups[group].true_positives += 1
                groups[group].flagged += 1
        if on_score is not None:
            on_score(txn, outcome)
    for metrics in result.by_rule.values():
        metrics.evaluated = result.overall.evaluated
    result.by_group = dict(groups)
    if first and last:
        result.days = max((last - first).total_seconds() / 86_400, 1 / 24)
    return result
