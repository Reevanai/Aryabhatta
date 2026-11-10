"""The rule expression language: validation and evaluation, one test per behaviour."""

from typing import Any

import pytest
from sentinel_txn_fraud.public import VOCABULARY, InvalidExpression, evaluate, validate

FIELDS = {
    "amount": 1500.0,
    "channel": "upi",
    "direction": "debit",
    "is_night": False,
    "purpose": None,
}
FEATURES = {"txn_count_1h": 3, "avg_debit_30d": None, "new_device": True, "account_age_days": 12.0}
PARAMS = {"large_amount": 20_000.0}


def run(expr: Any) -> bool:
    validate(expr, VOCABULARY)
    return evaluate(expr, FIELDS, FEATURES, PARAMS).hit


def op(name: str, *args: Any) -> dict[str, Any]:
    return {"op": name, "args": list(args)}


AMOUNT, COUNT, AVG = {"field": "amount"}, {"feature": "txn_count_1h"}, {"feature": "avg_debit_30d"}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (op("gt", AMOUNT, 1000), True),
        (op("gt", AMOUNT, 1500), False),
        (op("gte", AMOUNT, 1500), True),
        (op("lt", AMOUNT, 1500), False),
        (op("lte", AMOUNT, 1500), True),
        (op("eq", {"field": "channel"}, "upi"), True),
        (op("ne", {"field": "channel"}, "upi"), False),
        (op("between", AMOUNT, 1000, 1500), True),
        (op("between", AMOUNT, 1501, 2000), False),
        (op("in", {"field": "channel"}, ["imps", "upi"]), True),
        (op("in", {"field": "channel"}, ["cash"]), False),
        (op("and", op("gt", AMOUNT, 1), {"feature": "new_device"}), True),
        (op("and", op("gt", AMOUNT, 1), {"field": "is_night"}), False),
        (op("or", {"field": "is_night"}, {"feature": "new_device"}), True),
        (op("not", {"field": "is_night"}), True),
        (op("gt", op("add", AMOUNT, 500, 1), 2000), True),
        (op("eq", op("sub", AMOUNT, 500), 1000), True),
        (op("eq", op("mul", AMOUNT, 2), 3000), True),
        (op("eq", op("div", AMOUNT, 3), 500), True),
        (op("eq", op("min", AMOUNT, COUNT), 3), True),
        (op("eq", op("max", AMOUNT, COUNT), 1500), True),
        (op("gte", AMOUNT, op("mul", {"param": "large_amount"}, 0.05)), True),
    ],
)
def test_operations(expr: Any, expected: bool) -> None:
    assert run(expr) is expected


@pytest.mark.parametrize(
    "expr",
    [
        op("gt", AVG, 1),
        op("lt", AVG, 1),
        op("between", AVG, 0, 9),
        op("gt", op("mul", AVG, 2), 1),
        op("gt", op("div", AMOUNT, 0), 1),
        op("eq", AVG, 5),
    ],
)
def test_missing_values_never_match(expr: Any) -> None:
    assert run(expr) is False


def test_missing_value_can_be_tested_for_explicitly() -> None:
    assert run(op("eq", {"field": "purpose"}, None)) is True
    assert run(op("not", op("gt", AVG, 1))) is True


def test_evaluation_reports_every_value_it_read() -> None:
    expr = op(
        "or", op("gt", AMOUNT, 9_999), op("and", op("gte", COUNT, 3), {"feature": "new_device"})
    )
    result = evaluate(expr, FIELDS, FEATURES, PARAMS)
    assert result.hit
    assert result.observed == {"amount": 1500.0, "txn_count_1h": 3, "new_device": True}


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        (op("gt", {"field": "salary"}, 1), "unknown field 'salary'"),
        (op("gt", {"feature": "moon_phase"}, 1), "unknown feature 'moon_phase'"),
        (op("gt", AMOUNT, {"param": "nope"}), "unknown param 'nope'"),
        (op("exec", AMOUNT, 1), "unknown operation 'exec'"),
        (op("gt", AMOUNT, "big"), "'gt' needs number arguments"),
        (op("gt", AMOUNT), "'gt' takes 2 argument"),
        (op("and", op("gt", AMOUNT, 1)), "'and' takes at least 2"),
        (op("and", AMOUNT, COUNT), "'and' needs bool arguments"),
        (op("eq", {"field": "channel"}, 5), "compares values of the same type"),
        (op("in", {"field": "channel"}, []), "non-empty list"),
        (op("in", {"field": "channel"}, [1, 2]), "list items must be string"),
        (op("add", AMOUNT, 1), "must produce true or false"),
        (AMOUNT, "must produce true or false"),
        ({"field": "amount", "extra": 1}, "takes only a name"),
        ({"op": "gt"}, "needs exactly 'op' and 'args'"),
        ([1, 2], "expected a value"),
        ({"op": "gt", "args": [AMOUNT, 1], "then": "delete"}, "needs exactly 'op' and 'args'"),
    ],
)
def test_invalid_expressions_are_rejected_with_a_reason(expr: Any, message: str) -> None:
    with pytest.raises(InvalidExpression) as caught:
        validate(expr, VOCABULARY)
    assert message in str(caught.value)


def test_nesting_and_size_are_limited() -> None:
    deep: Any = {"field": "is_night"}
    for _ in range(20):
        deep = op("not", deep)
    with pytest.raises(InvalidExpression, match="nested too deeply"):
        validate(deep, VOCABULARY)
    wide = op("or", *[op("gt", AMOUNT, n) for n in range(120)])
    with pytest.raises(InvalidExpression, match="too large"):
        validate(wide, VOCABULARY)


def test_a_rule_cannot_carry_code() -> None:
    for attempt in (
        {"op": "gt", "args": [{"field": "__import__('os').system('id')"}, 1]},
        {"python": "open('/etc/passwd').read()"},
        op("eq", {"field": "channel"}, {"$where": "1"}),
    ):
        with pytest.raises(InvalidExpression):
            validate(attempt, VOCABULARY)
