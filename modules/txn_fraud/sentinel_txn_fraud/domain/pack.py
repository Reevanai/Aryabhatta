"""The starter rule pack. Tenants receive a copy as drafts they can edit; nothing is shared.

Each rule is ordinary data in the rule language. They are written with small helpers here only
for readability.
"""

from typing import Any

from sentinel_txn_fraud.domain.scoring import RuleSpec


def F(name: str) -> dict[str, str]:
    return {"field": name}


def X(name: str) -> dict[str, str]:
    return {"feature": name}


def P(name: str) -> dict[str, str]:
    return {"param": name}


def op(name: str, *args: Any) -> dict[str, Any]:
    return {"op": name, "args": list(args)}


def all_of(*args: Any) -> dict[str, Any]:
    return op("and", *args)


def any_of(*args: Any) -> dict[str, Any]:
    return op("or", *args)


DEBIT = op("eq", F("direction"), "debit")
CREDIT = op("eq", F("direction"), "credit")
NOT_FAILED = op("ne", F("status"), "failed")

STARTER_PACK: tuple[RuleSpec, ...] = (
    RuleSpec(
        "STRUCT-01",
        "Repeated cash deposits just under the threshold",
        all_of(
            op("eq", F("channel"), "cash"),
            CREDIT,
            op(
                "between",
                F("amount"),
                op("mul", P("structuring_threshold"), 0.7),
                P("structuring_threshold"),
            ),
            op("gte", X("cash_near_threshold_count_7d"), 4),
        ),
        "high",
        0.6,
    ),
    RuleSpec(
        "PASS-01",
        "Money received and passed on within minutes",
        all_of(
            DEBIT,
            NOT_FAILED,
            op("gte", X("credit_sum_30m"), P("pass_through_floor")),
            op("between", X("pass_through_ratio"), 0.8, 1.05),
            any_of(
                op("lt", X("account_age_days"), 90),
                op("gte", X("distinct_credit_sources_24h"), 3),
                op("gte", X("outflow_ratio_48h"), 0.9),
            ),
            op("lt", X("history_count_30d"), 40),
        ),
        "high",
        0.5,
    ),
    RuleSpec(
        "ATO-01",
        "Payments from a device new to an established account",
        all_of(
            DEBIT,
            F("is_transfer"),
            # A device that joined an account which already had another, within two hours.
            op("lte", X("device_age_minutes"), 120),
            op("gte", X("other_devices"), 1),
            op("gte", X("history_count_30d"), 5),
            any_of(
                op("gte", X("new_counterparties_1h"), 2),
                op("gte", X("drain_ratio_1h"), 0.5),
                all_of(F("is_night"), X("new_counterparty")),
            ),
        ),
        "critical",
        0.75,
    ),
    RuleSpec(
        "ATO-02",
        "New device and new network together",
        all_of(DEBIT, F("is_transfer"), X("new_device"), X("new_ip"), X("new_counterparty")),
        "high",
        0.45,
    ),
    RuleSpec(
        "APP-01",
        "Unusually large payment to a first-time payee",
        all_of(
            DEBIT,
            NOT_FAILED,
            F("is_transfer"),
            X("new_counterparty"),
            op("gte", F("amount"), P("large_amount")),
            op("gte", X("amount_to_avg_debit"), 10),
            op("gte", X("history_count_30d"), 8),
            op("eq", X("party_type"), "individual"),
            op("in", X("account_product"), ["savings"]),
            op("not", X("new_device")),
        ),
        "high",
        0.45,
    ),
    RuleSpec(
        "DRAIN-01",
        "Most of the balance paid out within an hour",
        all_of(
            DEBIT,
            NOT_FAILED,
            op("gte", X("drain_ratio_1h"), 0.8),
            op("gte", X("debit_count_1h"), 3),
            op("gte", X("debit_sum_1h"), P("large_amount")),
        ),
        "high",
        0.45,
    ),
    RuleSpec(
        "MULE-01",
        "Several payers into a young account",
        all_of(
            CREDIT,
            NOT_FAILED,
            op("gte", X("distinct_credit_sources_24h"), 3),
            op("gte", X("credit_sum_24h"), 30_000),
            op("lt", X("account_age_days"), 90),
        ),
        "high",
        0.5,
    ),
    RuleSpec(
        "DEV-01",
        "Device used on several accounts",
        all_of(DEBIT, op("gte", X("device_accounts"), 3)),
        "medium",
        0.3,
    ),
    RuleSpec(
        "LOAN-01",
        "Loan moved out soon after disbursement",
        all_of(
            DEBIT,
            NOT_FAILED,
            op("lte", X("minutes_since_loan_credit"), 1_440),
            op("gte", F("amount"), op("mul", X("loan_credit_amount"), 0.05)),
            any_of(op("eq", F("channel"), "cash"), X("new_counterparty")),
            op("gte", op("div", X("debit_sum_24h"), X("loan_credit_amount")), 0.5),
        ),
        "high",
        0.5,
    ),
    RuleSpec(
        "LAYER-01",
        "Large funds moved straight through a young account",
        all_of(
            DEBIT,
            NOT_FAILED,
            F("is_transfer"),
            op("gte", F("amount"), 100_000),
            op("lt", X("account_age_days"), 150),
            op("between", X("outflow_ratio_48h"), 0.7, 1.3),
            op("gte", X("credit_sum_48h"), 100_000),
        ),
        "high",
        0.55,
    ),
    RuleSpec(
        "VEL-01",
        "Burst of failed attempts",
        op("gte", X("failed_count_1h"), 4),
        "low",
        0.2,
    ),
)

for _rule in STARTER_PACK:
    _rule.check()
