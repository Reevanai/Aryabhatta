"""One test per validation rule on incoming records."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sentinel_ingestion.domain.models import (
    AccountRow,
    PartyRow,
    TransactionInput,
    safe_errors,
    transaction_from_csv,
)

from tests.synthetic import account_row, flat_txn, party_row, txn


def errors_for(model: Any, data: dict[str, Any]) -> dict[str, str]:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(data)
    return {e.field: e.message for e in safe_errors(caught.value)}


# Parties ---------------------------------------------------------------------------


def test_valid_party() -> None:
    row = PartyRow.model_validate(party_row(1))
    assert row.pan == "ABCPT0001Z"


def test_party_optional_fields_may_be_blank() -> None:
    blanks = {
        "pan": "",
        "phone": " ",
        "email": "",
        "address": "",
        "date_of_birth": "",
        "kyc_risk_category": "",
        "onboarded_on": "",
    }
    row = PartyRow.model_validate(party_row(1, **blanks))
    assert row.pan is None
    assert row.date_of_birth is None
    assert row.kyc_risk_category is None


def test_party_pan_is_upper_cased() -> None:
    assert PartyRow.model_validate(party_row(1, pan="abcpt0001z")).pan == "ABCPT0001Z"


@pytest.mark.parametrize("pan", ["ABCP0001Z", "ABCPT00012", "1BCPT0001Z", "ABCPT0001ZZ"])
def test_party_pan_format(pan: str) -> None:
    assert "pan" in errors_for(PartyRow, party_row(1, pan=pan))


@pytest.mark.parametrize("phone", ["12345", "5000000001", "90000000011", "phone"])
def test_party_phone_format(phone: str) -> None:
    assert "phone" in errors_for(PartyRow, party_row(1, phone=phone))


@pytest.mark.parametrize("phone", ["9000000001", "+919000000001", "91 9000000001"])
def test_party_phone_accepted_forms(phone: str) -> None:
    PartyRow.model_validate(party_row(1, phone=phone))


@pytest.mark.parametrize("email", ["nobody", "a@b", "a b@c.d"])
def test_party_email_format(email: str) -> None:
    assert "email" in errors_for(PartyRow, party_row(1, email=email))


def test_party_type_is_restricted() -> None:
    assert "party_type" in errors_for(PartyRow, party_row(1, party_type="robot"))


def test_party_risk_category_is_restricted() -> None:
    assert "kyc_risk_category" in errors_for(PartyRow, party_row(1, kyc_risk_category="extreme"))


def test_party_requires_customer_id_and_name() -> None:
    found = errors_for(PartyRow, party_row(1, customer_id="", full_name=""))
    assert {"customer_id", "full_name"} <= set(found)


def test_party_dates_must_be_valid_and_not_in_the_future() -> None:
    tomorrow = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    assert "date_of_birth" in errors_for(PartyRow, party_row(1, date_of_birth="15/01/1990"))
    assert "date_of_birth" in errors_for(PartyRow, party_row(1, date_of_birth=tomorrow))
    assert "onboarded_on" in errors_for(PartyRow, party_row(1, onboarded_on=tomorrow))


def test_party_unknown_columns_are_rejected() -> None:
    assert "salary" in errors_for(PartyRow, {**party_row(1), "salary": "1"})


# Accounts ----------------------------------------------------------------------------


def test_valid_account() -> None:
    assert AccountRow.model_validate(account_row(1)).currency == "INR"


def test_account_product_and_status_are_restricted() -> None:
    assert "product_type" in errors_for(AccountRow, account_row(1, product_type="crypto"))
    assert "status" in errors_for(AccountRow, account_row(1, status="open"))


def test_account_currency_format() -> None:
    assert "currency" in errors_for(AccountRow, account_row(1, currency="rupees"))


def test_account_requires_references() -> None:
    found = errors_for(
        AccountRow, account_row(1, account_number="", customer_id="", branch_code="")
    )
    assert {"account_number", "customer_id", "branch_code"} <= set(found)


# Transactions --------------------------------------------------------------------------


def test_valid_transaction_is_normalised_to_utc() -> None:
    parsed = TransactionInput.model_validate(txn(1, booked_at="2026-01-01T10:00:00+05:30"))
    assert parsed.booked_at == datetime(2026, 1, 1, 4, 30, tzinfo=UTC)
    assert parsed.ip_address == "203.0.113.7"


@pytest.mark.parametrize("amount", ["0", "-5", "10.001", "abc", ""])
def test_transaction_amount_rules(amount: str) -> None:
    found = errors_for(TransactionInput, txn(1, amount={"amount": amount, "currency": "INR"}))
    assert "amount.amount" in found


def test_transaction_currency_format() -> None:
    found = errors_for(TransactionInput, txn(1, amount={"amount": "1.00", "currency": "inr"}))
    assert "amount.currency" in found


def test_transaction_time_needs_a_zone() -> None:
    assert "booked_at" in errors_for(TransactionInput, txn(1, booked_at="2026-01-01T10:00:00"))


def test_transaction_time_must_not_be_far_in_the_future() -> None:
    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    assert "booked_at" in errors_for(TransactionInput, txn(1, booked_at=future))


@pytest.mark.parametrize(
    "field,value", [("channel", "pigeon"), ("direction", "sideways"), ("status", "maybe")]
)
def test_transaction_enumerations(field: str, value: str) -> None:
    assert field in errors_for(TransactionInput, txn(1, **{field: value}))


def test_transaction_ip_address_must_parse() -> None:
    assert "ip_address" in errors_for(TransactionInput, txn(1, ip_address="999.1.1.1"))
    assert (
        TransactionInput.model_validate(txn(1, ip_address="2001:db8::1")).ip_address
        == "2001:db8::1"
    )


def test_transaction_vpa_shape() -> None:
    assert "counterparty_vpa" in errors_for(TransactionInput, txn(1, counterparty_vpa="no-at-sign"))


def test_upi_transaction_needs_a_counterparty() -> None:
    found = errors_for(TransactionInput, txn(1, counterparty_vpa=None))
    assert "row" in found
    TransactionInput.model_validate(
        txn(1, counterparty_vpa=None, counterparty_account_number="998877665544")
    )


def test_cash_transaction_needs_no_counterparty() -> None:
    TransactionInput.model_validate(txn(1, channel="cash", counterparty_vpa=None))


def test_transaction_requires_reference_and_account() -> None:
    found = errors_for(TransactionInput, txn(1, source_reference="", account_number=""))
    assert {"source_reference", "account_number"} <= set(found)


def test_flat_csv_row_is_converted() -> None:
    parsed = transaction_from_csv(flat_txn(1))
    assert str(parsed.amount.amount) == "2500.50"
    assert parsed.amount.currency == "INR"
    assert parsed.device_id is None


def test_flat_csv_row_with_missing_amount_is_an_error() -> None:
    row = flat_txn(1)
    del row["amount"]
    with pytest.raises(ValidationError):
        transaction_from_csv(row)


# Error hygiene ---------------------------------------------------------------------------


def test_error_messages_never_contain_the_rejected_value() -> None:
    secrets = {
        "pan": "ZZZZZ9999Q9",
        "phone": "1234567",
        "email": "leaky-value",
        "party_type": "leaky-type",
        "date_of_birth": "31-31-1990",
    }
    with pytest.raises(ValidationError) as caught:
        PartyRow.model_validate(party_row(1, **secrets))
    rendered = " ".join(f"{e.field} {e.rule} {e.message}" for e in safe_errors(caught.value))
    for value in secrets.values():
        assert value not in rendered
