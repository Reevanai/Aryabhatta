"""Features, the starter rules, scoring and replay."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sentinel_txn_fraud.public import (
    FEATURES,
    STARTER_PACK,
    VOCABULARY,
    AccountProfile,
    MemoryState,
    RuleSpec,
    Snapshot,
    Thresholds,
    TxnView,
    compute,
    replay,
    score,
)

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)  # 14:30 IST
OLD = AccountProfile(opened_on=T0 - timedelta(days=900), product="savings", kyc_risk="low")
YOUNG = AccountProfile(opened_on=T0 - timedelta(days=20), product="savings")
PARAMS = Thresholds().params
RULES = {rule.code: rule for rule in STARTER_PACK}


def txn(
    minutes: float = 0,
    amount: float = 500,
    direction: str = "debit",
    channel: str = "upi",
    **kw: Any,
) -> TxnView:
    kw.setdefault("counterparty", "cp-1")
    return TxnView(
        ts=T0 + timedelta(minutes=minutes),
        amount=amount,
        channel=channel,
        direction=direction,
        status=kw.pop("status", "completed"),
        account=kw.pop("account", "acct-1"),
        reference=kw.pop("reference", f"r{minutes}-{amount}"),
        **kw,
    )


class Account:
    """Feeds transactions through the in-memory state the way live scoring does."""

    def __init__(self, profile: AccountProfile = OLD) -> None:
        self.state = MemoryState()
        self.profile = profile

    def features(self, t: TxnView) -> dict[str, Any]:
        return compute(t, self.state.snapshot_sync("t", t), self.profile, dict(PARAMS))

    def add(self, *txns: TxnView) -> "Account":
        for t in txns:
            self.state.apply_sync("t", t)
        return self

    def fired(self, t: TxnView) -> list[str]:
        outcome = score(
            t, self.state.snapshot_sync("t", t), self.profile, STARTER_PACK, Thresholds()
        )
        return outcome.fired

    def background(self, n: int = 12, every_hours: float = 48) -> "Account":
        """Ordinary earlier activity on a known device, so the account is established."""
        for i in reversed(range(n)):
            self.add(
                txn(
                    -60 * every_hours * (i + 1),
                    300 + i,
                    counterparty=f"shop-{i % 4}",
                    device="phone",
                    ip="home",
                )
            )
        return self


# Features ---------------------------------------------------------------------------------


def test_every_feature_is_computed_and_in_the_vocabulary() -> None:
    computed = Account().features(txn())
    assert set(computed) == set(FEATURES) == set(VOCABULARY.features)


def test_velocity_counts_include_the_current_transaction_and_respect_windows() -> None:
    a = Account().add(
        txn(-30, 100), txn(-90, 200), txn(-60 * 25, 400), txn(-10, 50, status="failed")
    )
    f = a.features(txn(0, 1000))
    assert (f["txn_count_1h"], f["txn_count_24h"]) == (3, 4)
    assert (f["debit_count_1h"], f["debit_sum_1h"], f["debit_sum_24h"]) == (2, 1100, 1300)
    assert f["debit_sum_48h"] == 1700
    assert f["failed_count_1h"] == 1


def test_failed_transactions_do_not_count_as_money_moved() -> None:
    a = Account().add(txn(-5, 9000, status="failed"))
    f = a.features(txn(0, 100, status="failed"))
    assert (f["debit_sum_1h"], f["debit_count_1h"], f["failed_count_1h"]) == (0, 0, 2)


def test_history_statistics_exclude_the_current_transaction() -> None:
    a = Account().add(txn(-100, 100), txn(-200, 300))
    f = a.features(txn(0, 2000))
    assert (f["avg_debit_30d"], f["max_debit_30d"], f["history_count_30d"]) == (200, 300, 2)
    assert f["amount_to_avg_debit"] == 10
    empty = Account().features(txn(0, 2000))
    assert empty["avg_debit_30d"] is None
    assert empty["amount_to_avg_debit"] is None


def test_events_older_than_thirty_days_are_ignored() -> None:
    a = Account().add(txn(-60 * 24 * 31, 5000))
    assert a.features(txn())["history_count_30d"] == 0


def test_cash_structuring_features() -> None:
    a = Account().add(
        txn(-60 * 24, 45_000, "credit", "cash"),
        txn(-60 * 48, 49_000, "credit", "cash"),
        txn(-60 * 72, 10_000, "credit", "cash"),
        txn(-60 * 24 * 8, 48_000, "credit", "cash"),
        txn(-60, 47_000, "credit", "upi"),
    )
    f = a.features(txn(0, 36_000, "credit", "cash"))
    assert (f["cash_credit_count_7d"], f["cash_near_threshold_count_7d"]) == (4, 3)
    assert f["cash_credit_sum_7d"] == 140_000
    # Exactly at the threshold is not "under" it; just under 70% is not "near".
    assert a.features(txn(0, 50_000, "credit", "cash"))["cash_near_threshold_count_7d"] == 2
    assert a.features(txn(0, 34_999, "credit", "cash"))["cash_near_threshold_count_7d"] == 2


def test_counterparty_features() -> None:
    a = Account().add(txn(-60 * 5, 100, counterparty="old"), txn(-20, 100, counterparty="fresh-a"))
    f = a.features(txn(0, 100, counterparty="fresh-b"))
    assert f["new_counterparty"] is True
    assert f["new_counterparties_1h"] == 2
    assert a.features(txn(0, 100, counterparty="old"))["new_counterparty"] is False
    assert a.features(txn(0, 100, counterparty=None))["new_counterparty"] is False


def test_credit_source_and_pass_through_features() -> None:
    a = Account().add(
        txn(-20, 10_000, "credit", counterparty="p1"),
        txn(-10, 5_000, "credit", counterparty="p2"),
        txn(-40, 2_000, "credit", counterparty="p3"),
        txn(-60 * 30, 900, "credit", counterparty="p4"),
    )
    f = a.features(txn(0, 14_000))
    assert f["distinct_credit_sources_24h"] == 3
    assert f["credit_sum_30m"] == 15_000
    assert f["pass_through_ratio"] == pytest.approx(14_000 / 15_000)
    assert (f["minutes_since_last_credit"], f["last_credit_amount"]) == (10, 5_000)
    assert f["outflow_ratio_48h"] == pytest.approx(14_000 / 17_900)
    credit = a.features(txn(0, 1, "credit", counterparty="p9"))
    assert credit["pass_through_ratio"] is None
    assert credit["distinct_credit_sources_24h"] == 4


def test_drain_ratio_uses_the_balance_an_hour_ago() -> None:
    a = Account().add(txn(-30, 40_000), txn(-20, 30_000))
    f = a.features(txn(0, 20_000, balance_after=10_000))
    # An hour ago the balance was 10,000 + 90,000 paid out since.
    assert f["drain_ratio_1h"] == pytest.approx(0.9)
    assert a.features(txn(0, 20_000))["drain_ratio_1h"] is None


def test_loan_disbursement_features() -> None:
    a = Account().add(txn(-120, 200_000, "credit", "internal", purpose="loan_disbursement"))
    f = a.features(txn(0, 50_000, channel="cash"))
    assert (f["minutes_since_loan_credit"], f["loan_credit_amount"]) == (120, 200_000)
    assert Account().features(txn())["minutes_since_loan_credit"] is None


def test_device_and_network_features() -> None:
    a = Account().add(txn(-600, device="phone", ip="home"))
    known = a.features(txn(0, device="phone", ip="home"))
    assert (known["new_device"], known["new_ip"], known["device_age_minutes"]) == (
        False,
        False,
        600,
    )
    strange = a.features(txn(0, device="other", ip="elsewhere"))
    assert (strange["new_device"], strange["new_ip"], strange["device_age_minutes"]) == (
        True,
        True,
        0,
    )
    # The very first device on an account is not "new": there is nothing to compare it with.
    first = Account().features(txn(0, device="phone", ip="home"))
    assert (first["new_device"], first["new_ip"]) == (False, False)
    assert a.features(txn(0, channel="cash"))["device_accounts"] is None


def test_device_shared_across_accounts_is_counted() -> None:
    state = MemoryState()
    for n in range(3):
        state.apply_sync("t", txn(-10 * n, account=f"acct-{n}", device="shared"))
    snap = state.snapshot_sync("t", txn(account="acct-9", device="shared"))
    assert snap.device_accounts == 4
    assert state.snapshot_sync("t", txn(account="acct-1", device="shared")).device_accounts == 3
    assert (
        state.snapshot_sync("other-tenant", txn(account="acct-9", device="shared")).device_accounts
        == 1
    )


def test_account_profile_features() -> None:
    f = Account(YOUNG).features(txn())
    assert f["account_age_days"] == pytest.approx(20)
    assert (f["account_product"], f["party_type"]) == ("savings", "individual")
    assert compute(txn(), Snapshot(), AccountProfile(), dict(PARAMS))["account_age_days"] is None


def test_history_is_capped() -> None:
    a = Account()
    for i in range(300):
        a.add(txn(-i, 1))
    assert a.features(txn())["history_count_30d"] == 256


# Starter rules: each fires on its pattern and stays quiet on the ordinary version --------------


def test_struct_01() -> None:
    a = Account()
    for day in range(1, 4):
        a.add(txn(-60 * 24 * day, 46_000, "credit", "cash"))
    assert "STRUCT-01" in a.fired(txn(0, 47_500, "credit", "cash"))
    assert "STRUCT-01" not in a.fired(txn(0, 52_000, "credit", "cash"))
    assert "STRUCT-01" not in Account().fired(txn(0, 47_500, "credit", "cash"))


def test_pass_01() -> None:
    mule = Account(YOUNG).add(txn(-10, 40_000, "credit", counterparty="victim"))
    assert "PASS-01" in mule.fired(txn(0, 38_000, counterparty="next"))
    assert "PASS-01" not in mule.fired(txn(0, 4_000, counterparty="next"))
    busy = (
        Account()
        .background(60, every_hours=6)
        .add(txn(-10, 40_000, "credit", counterparty="customer"))
    )
    assert "PASS-01" not in busy.fired(txn(0, 38_000, counterparty="supplier"))


def test_ato_01_and_ato_02() -> None:
    victim = Account().background()
    first = txn(0, 100, device="attacker", ip="far", counterparty="t0")
    assert "ATO-02" in victim.fired(first)
    victim.add(first, txn(2, 20_000, device="attacker", ip="far", counterparty="t1"))
    drain = txn(5, 25_000, device="attacker", ip="far", counterparty="t2", balance_after=2_000)
    assert "ATO-01" in victim.fired(drain)
    # The customer's own phone paying two new shops is not a takeover.
    own = Account().background().add(txn(2, 2_000, device="phone", ip="home", counterparty="n1"))
    assert not {"ATO-01", "ATO-02"} & set(
        own.fired(txn(5, 2_500, device="phone", ip="home", counterparty="n2"))
    )


def test_app_01() -> None:
    victim = Account().background()
    scam = txn(0, 60_000, channel="imps", device="phone", ip="home", counterparty="new-payee")
    assert "APP-01" in victim.fired(scam)
    assert "APP-01" not in victim.fired(
        txn(0, 60_000, channel="imps", device="phone", counterparty="shop-1")
    )
    assert "APP-01" not in victim.fired(txn(0, 900, device="phone", counterparty="new-payee"))


def test_drain_01() -> None:
    a = Account().add(txn(-30, 30_000), txn(-20, 30_000))
    assert "DRAIN-01" in a.fired(txn(0, 30_000, balance_after=5_000))
    assert "DRAIN-01" not in a.fired(txn(0, 30_000, balance_after=900_000))


def test_mule_01() -> None:
    mule = Account(YOUNG).add(
        txn(-60, 15_000, "credit", counterparty="v1"), txn(-30, 12_000, "credit", counterparty="v2")
    )
    assert "MULE-01" in mule.fired(txn(0, 9_000, "credit", counterparty="v3"))
    trader = Account(OLD).add(
        txn(-60, 15_000, "credit", counterparty="v1"), txn(-30, 12_000, "credit", counterparty="v2")
    )
    assert "MULE-01" not in trader.fired(txn(0, 9_000, "credit", counterparty="v3"))


def test_dev_01() -> None:
    state = MemoryState()
    for n in range(3):
        state.apply_sync("t", txn(-10 * (n + 1), account=f"acct-{n}", device="shared"))
    t = txn(0, account="acct-0", device="shared")
    assert "DEV-01" in score(t, state.snapshot_sync("t", t), OLD, STARTER_PACK, Thresholds()).fired
    solo = txn(0, account="acct-7", device="mine")
    assert (
        "DEV-01"
        not in score(solo, state.snapshot_sync("t", solo), OLD, STARTER_PACK, Thresholds()).fired
    )


def test_loan_01() -> None:
    a = Account(YOUNG).add(txn(-90, 200_000, "credit", "internal", purpose="loan_disbursement"))
    a.add(txn(-60, 90_000, channel="cash", counterparty=None))
    assert "LOAN-01" in a.fired(txn(0, 20_000, channel="cash", counterparty=None))
    assert "LOAN-01" not in Account(YOUNG).add(
        txn(-90, 200_000, "credit", "internal", purpose="loan_disbursement")
    ).fired(txn(0, 2_000, counterparty="shop"))


def test_layer_01() -> None:
    shell = Account(YOUNG).add(txn(-600, 900_000, "credit", "rtgs", counterparty="source"))
    assert "LAYER-01" in shell.fired(txn(0, 700_000, channel="neft", counterparty="layer-1"))
    assert "LAYER-01" not in Account(OLD).add(
        txn(-600, 900_000, "credit", "rtgs", counterparty="source")
    ).fired(txn(0, 700_000, channel="neft", counterparty="layer-1"))


def test_vel_01() -> None:
    a = Account()
    for i in range(3):
        a.add(txn(-i - 1, 100, status="failed"))
    assert "VEL-01" in a.fired(txn(0, 100, status="failed"))
    assert "VEL-01" not in Account().fired(txn(0, 100, status="failed"))


def test_every_starter_rule_has_a_test_and_validates() -> None:
    tested = {
        "STRUCT-01",
        "PASS-01",
        "ATO-01",
        "ATO-02",
        "APP-01",
        "DRAIN-01",
        "MULE-01",
        "DEV-01",
        "LOAN-01",
        "LAYER-01",
        "VEL-01",
    }
    assert set(RULES) == tested
    for rule in STARTER_PACK:
        rule.check()


# Scoring ------------------------------------------------------------------------------------

ALWAYS = {"op": "gt", "args": [{"field": "amount"}, 0]}


def _score(rules: list[RuleSpec], thresholds: Thresholds = Thresholds(), **kw: Any) -> Any:  # noqa: B008
    t = txn(**kw)
    return score(t, Snapshot(), OLD, rules, thresholds)


def test_no_hits_scores_zero_and_allows() -> None:
    outcome = _score([RuleSpec("A", "a", {"op": "gt", "args": [{"field": "amount"}, 9e9]})])
    assert (outcome.score, outcome.recommendation, outcome.reasons) == (0.0, "allow", [])


def test_hits_combine_so_that_several_moderate_signals_add_up() -> None:
    rules = [RuleSpec("A", "a", ALWAYS, weight=0.3), RuleSpec("B", "b", ALWAYS, weight=0.3)]
    assert _score(rules[:1]).recommendation == "allow"
    both = _score(rules)
    assert both.score == pytest.approx(0.51)
    assert both.recommendation == "review"
    assert _score([RuleSpec("A", "a", ALWAYS, weight=0.8)]).recommendation == "hold_for_review"


def test_dry_run_rules_are_reported_but_do_not_count() -> None:
    outcome = _score([RuleSpec("A", "a", ALWAYS, weight=0.9, dry_run=True)])
    assert (outcome.score, outcome.recommendation, outcome.fired) == (0.0, "allow", [])
    assert [(r.code, r.dry_run, r.contribution) for r in outcome.reasons] == [("A", True, 0.0)]


def test_reasons_carry_the_values_that_triggered_them() -> None:
    outcome = _score([RuleSpec("A", "a", ALWAYS, weight=0.5, version_id="v1")], amount=1234.5)
    (reason,) = outcome.reasons
    assert (reason.kind, reason.code, reason.rule_version_id, reason.observed) == (
        "rule",
        "A",
        "v1",
        {"amount": 1234.5},
    )


def test_channel_restriction() -> None:
    rule = RuleSpec("A", "a", ALWAYS, weight=0.5, channels=("cash",))
    assert _score([rule], channel="upi").fired == []
    assert _score([rule], channel="cash").fired == ["A"]


def test_thresholds_are_configurable() -> None:
    rule = RuleSpec("A", "a", ALWAYS, weight=0.3)
    assert _score([rule], Thresholds(review=0.25, hold=0.9)).recommendation == "review"


@pytest.mark.parametrize(("weight", "severity"), [(0, "high"), (1.5, "high"), (0.5, "apocalyptic")])
def test_rule_specification_is_checked(weight: float, severity: str) -> None:
    with pytest.raises(ValueError, match=r"weight|severity"):
        RuleSpec("A", "a", ALWAYS, severity, weight).check()


# Replay --------------------------------------------------------------------------------------


def test_replay_measures_precision_and_recall_against_labels() -> None:
    rule = RuleSpec("BIG", "big", {"op": "gte", "args": [{"field": "amount"}, 1000]}, weight=0.9)
    history = [
        txn(i, amount, reference=f"r{i}") for i, amount in enumerate([10, 5000, 20, 7000, 9000, 30])
    ]
    fraud = {"r1": "scam", "r4": "scam", "r5": "mule"}
    result = replay(history, [rule], Thresholds(), {}, fraud=fraud)
    o = result.overall
    assert (o.evaluated, o.flagged, o.true_positives, o.false_positives, o.false_negatives) == (
        6,
        3,
        2,
        1,
        1,
    )
    assert o.precision == pytest.approx(2 / 3)
    assert o.recall == pytest.approx(2 / 3)
    assert result.by_group["scam"].recall == 1.0
    assert result.by_group["mule"].recall == 0.0
    assert result.by_rule["BIG"].flagged == 3
    assert result.flagged_refs == {"r1", "r3", "r4"}


def test_replay_without_labels_reports_no_precision() -> None:
    result = replay([txn(0, 5)], list(STARTER_PACK), Thresholds(), {})
    assert (result.overall.precision, result.overall.recall, result.overall.evaluated) == (
        None,
        None,
        1,
    )
