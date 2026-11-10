"""Validated shapes of incoming records. Pure: no I/O.

Error messages must never contain the offending value, because rejected rows hold personal
data and the messages are stored and shown in clear.
"""

import ipaddress
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

Dataset = Literal["parties", "accounts", "loans", "devices", "transactions"]
# In the order they must be loaded: each depends on the ones before it.
DATASETS: tuple[Dataset, ...] = ("parties", "accounts", "loans", "devices", "transactions")

Channel = Literal[
    "upi", "imps", "neft", "rtgs", "card", "cash", "cheque", "aeps", "internal", "other"
]

_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_PHONE = re.compile(r"^(?:\+?91[\s-]?)?[6-9][0-9]{9}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
MAX_FUTURE = timedelta(days=1)


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


Optional_ = BeforeValidator(_blank_to_none)
OptStr = Annotated[str | None, Optional_]
OptDate = Annotated[date | None, Optional_]


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class PartyRow(_Row):
    customer_id: str = Field(min_length=1, max_length=64)
    party_type: Literal["individual", "organisation"]
    full_name: str = Field(min_length=1, max_length=200)
    pan: OptStr = None
    phone: OptStr = None
    email: OptStr = None
    address: Annotated[Annotated[str, Field(max_length=500)] | None, Optional_] = None
    date_of_birth: OptDate = None
    kyc_risk_category: Annotated[Literal["low", "medium", "high"] | None, Optional_] = None
    onboarded_on: OptDate = None

    @field_validator("pan")
    @classmethod
    def _pan(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper()
        if not _PAN.match(v):
            raise ValueError("must be five letters, four digits and one letter")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is not None and not _PHONE.match(v):
            raise ValueError("must be a ten-digit Indian mobile number")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is not None and (len(v) > 254 or not _EMAIL.match(v)):
            raise ValueError("must be an email address")
        return v

    @field_validator("date_of_birth", "onboarded_on")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and v > datetime.now(UTC).date():
            raise ValueError("must not be in the future")
        return v


class AccountRow(_Row):
    account_number: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    branch_code: str = Field(min_length=1, max_length=32)
    product_type: Literal["savings", "current", "term_deposit", "loan", "overdraft", "other"]
    status: Literal["active", "dormant", "frozen", "closed"]
    opened_on: OptDate = None
    currency: str = "INR"

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        if not _CURRENCY.match(v):
            raise ValueError("must be a three-letter currency code")
        return v


class LoanRow(_Row):
    loan_id: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    account_number: str = Field(min_length=1, max_length=64)
    product: Literal[
        "gold", "personal", "two_wheeler", "agri_kcc", "msme", "housing", "vehicle", "other"
    ]
    principal: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    annual_interest_rate: Decimal = Field(ge=0, le=100, max_digits=6, decimal_places=3)
    tenure_months: int = Field(ge=1, le=480)
    disbursed_on: date
    emi_amount: Annotated[
        Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)] | None, Optional_
    ] = None
    status: Literal["active", "closed", "overdue", "written_off"]
    days_past_due: int = Field(default=0, ge=0)
    currency: str = "INR"

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        if not _CURRENCY.match(v):
            raise ValueError("must be a three-letter currency code")
        return v

    @field_validator("disbursed_on")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > datetime.now(UTC).date():
            raise ValueError("must not be in the future")
        return v

    @model_validator(mode="after")
    def _arrears_match_status(self) -> "LoanRow":
        if self.days_past_due > 0 and self.status in {"active", "closed"}:
            raise ValueError("a loan with days past due must be overdue or written off")
        if self.status == "overdue" and self.days_past_due == 0:
            raise ValueError("an overdue loan needs days past due")
        return self


class DeviceRow(_Row):
    """One customer's use of one device. A device may appear once per customer."""

    device_id: str = Field(min_length=1, max_length=255)
    customer_id: str = Field(min_length=1, max_length=64)
    platform: Literal["android", "ios", "web", "other"]
    os_version: Annotated[Annotated[str, Field(max_length=32)] | None, Optional_] = None
    rooted: bool = False
    first_seen: datetime

    @field_validator("first_seen")
    @classmethod
    def _first_seen(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("must include a time zone")
        if v > datetime.now(UTC) + MAX_FUTURE:
            raise ValueError("must not be more than one day in the future")
        return v.astimezone(UTC)


class Money(_Row):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = "INR"

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        if not _CURRENCY.match(v):
            raise ValueError("must be a three-letter currency code")
        return v


class TransactionInput(_Row):
    """A transaction as sent by the bank, over HTTP, Kafka or in a batch file."""

    source_reference: str = Field(min_length=1, max_length=128)
    channel: Channel
    direction: Literal["debit", "credit"]
    amount: Money
    booked_at: datetime
    account_number: str = Field(min_length=1, max_length=64)
    branch_code: Annotated[Annotated[str, Field(max_length=32)] | None, Optional_] = None
    counterparty_account_number: Annotated[
        Annotated[str, Field(max_length=64)] | None, Optional_
    ] = None
    counterparty_vpa: Annotated[Annotated[str, Field(max_length=255)] | None, Optional_] = None
    counterparty_bank_code: Annotated[Annotated[str, Field(max_length=32)] | None, Optional_] = None
    device_id: Annotated[Annotated[str, Field(max_length=255)] | None, Optional_] = None
    ip_address: OptStr = None
    merchant_category: Annotated[Annotated[str, Field(max_length=16)] | None, Optional_] = None
    status: Literal["attempted", "completed", "failed", "reversed"] = "attempted"
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("booked_at")
    @classmethod
    def _booked_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("must include a time zone")
        if v > datetime.now(UTC) + MAX_FUTURE:
            raise ValueError("must not be more than one day in the future")
        return v.astimezone(UTC)

    @field_validator("ip_address")
    @classmethod
    def _ip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            return str(ipaddress.ip_address(v))
        except ValueError:
            raise ValueError("must be an IP address") from None

    @field_validator("counterparty_vpa")
    @classmethod
    def _vpa(cls, v: str | None) -> str | None:
        if v is not None and v.count("@") != 1:
            raise ValueError("must be a virtual payment address")
        return v

    @model_validator(mode="after")
    def _channel_rules(self) -> "TransactionInput":
        if self.channel == "upi" and not (
            self.counterparty_vpa or self.counterparty_account_number
        ):
            raise ValueError("a UPI transaction needs a counterparty address or account")
        return self


_TXN_FLAT = {"amount", "currency", "attributes"}


def transaction_from_csv(row: dict[str, str]) -> TransactionInput:
    """Build a transaction from a flat CSV row.

    Amount and currency are separate columns; `attributes`, if present, is a JSON object.
    """
    data: dict[str, Any] = {k: v for k, v in row.items() if k not in _TXN_FLAT}
    data["amount"] = {"amount": row.get("amount", ""), "currency": row.get("currency") or "INR"}
    raw_attributes = (row.get("attributes") or "").strip()
    if raw_attributes:
        try:
            data["attributes"] = json.loads(raw_attributes)
        except ValueError:
            # Let validation report it against the field, without echoing the content.
            data["attributes"] = "not a JSON object"
    return TransactionInput.model_validate(data)


class RowError(BaseModel):
    field: str
    rule: str
    message: str


def safe_errors(exc: ValidationError) -> list[RowError]:
    """Validation errors without the input values."""
    out: list[RowError] = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(part) for part in err["loc"]) or "row"
        message = err["msg"].removeprefix("Value error, ")
        out.append(RowError(field=loc, rule=err["type"], message=message))
    return out
