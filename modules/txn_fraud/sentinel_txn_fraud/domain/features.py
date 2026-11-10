"""Features: what a rule can know about a transaction and its history.

One implementation serves live scoring, stream scoring and backtests. Live scoring reads the
account's recent history from the online store; a backtest replays history through the same
functions in memory. There is no second definition to drift from the first.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_LIMIT = 256  # most recent events kept per account
HISTORY_DAYS = 30
TRANSFER_CHANNELS = ("upi", "imps", "neft", "rtgs")


@dataclass(frozen=True)
class TxnView:
    """The transaction being scored. Identifiers are tokens."""

    ts: datetime
    amount: float
    channel: str
    direction: str
    status: str
    account: str
    counterparty: str | None = None
    device: str | None = None
    ip: str | None = None
    purpose: str | None = None
    balance_after: float | None = None
    reference: str = ""
    transaction_id: str | None = None


@dataclass(frozen=True)
class AccountProfile:
    opened_on: datetime | None = None
    product: str = "savings"
    kyc_risk: str | None = None
    party_type: str = "individual"


@dataclass
class Event:
    """One past transaction, as kept in an account's history."""

    ts: float
    amount: float
    credit: bool
    channel: str
    counterparty: str | None
    completed: bool
    purpose: str | None

    def pack(self) -> list[Any]:
        return [
            self.ts,
            self.amount,
            int(self.credit),
            self.channel,
            self.counterparty,
            int(self.completed),
            self.purpose,
        ]

    @classmethod
    def unpack(cls, raw: Sequence[Any]) -> "Event":
        return cls(raw[0], raw[1], bool(raw[2]), raw[3], raw[4], bool(raw[5]), raw[6])

    @classmethod
    def of(cls, txn: TxnView) -> "Event":
        return cls(
            txn.ts.timestamp(),
            txn.amount,
            txn.direction == "credit",
            txn.channel,
            txn.counterparty,
            txn.status != "failed",
            txn.purpose,
        )


@dataclass
class Snapshot:
    """What the online store knows about the account at the moment of scoring."""

    history: list[Event] = field(default_factory=list)  # newest first, excluding this txn
    counterparty_known: bool = False
    device_first_seen: float | None = None  # this device on this account
    known_devices: int = 0
    device_accounts: int = 0  # accounts this device has been seen on, including this one
    ip_known: bool = False
    known_ips: int = 0


# name -> (type, description). This is the vocabulary a rule author sees.
FIELDS: dict[str, tuple[str, str]] = {
    "amount": ("number", "Transaction amount in rupees"),
    "channel": ("string", "upi, imps, neft, rtgs, card, cash, cheque, aeps, internal, other"),
    "direction": ("string", "debit or credit"),
    "status": ("string", "attempted, completed, failed or reversed"),
    "purpose": ("string", "Purpose code supplied by the bank, if any"),
    "hour": ("number", "Hour of day in Indian Standard Time, 0-23"),
    "is_night": ("bool", "Between midnight and 05:00 IST"),
    "is_transfer": ("bool", "Channel is UPI, IMPS, NEFT or RTGS"),
    "has_device": ("bool", "The transaction carries a device identifier"),
    "has_counterparty": ("bool", "The transaction names a counterparty"),
    "balance_after": ("number", "Account balance after the transaction, if supplied"),
}
FEATURES: dict[str, tuple[str, str]] = {
    "account_age_days": ("number", "Days since the account was opened"),
    "account_product": ("string", "Account product type"),
    "party_type": ("string", "individual or organisation"),
    "kyc_risk": ("string", "Customer risk category: low, medium or high"),
    "txn_count_1h": ("number", "Transactions on the account in the last hour, including this one"),
    "txn_count_24h": ("number", "Transactions in the last 24 hours, including this one"),
    "debit_count_1h": ("number", "Debits in the last hour, including this one"),
    "debit_sum_1h": ("number", "Value of debits in the last hour, including this one"),
    "debit_sum_24h": ("number", "Value of debits in the last 24 hours, including this one"),
    "debit_sum_48h": ("number", "Value of debits in the last 48 hours, including this one"),
    "credit_sum_24h": ("number", "Value of credits in the last 24 hours, including this one"),
    "credit_sum_48h": ("number", "Value of credits in the last 48 hours, including this one"),
    "failed_count_1h": ("number", "Failed transactions in the last hour, including this one"),
    "cash_credit_count_7d": ("number", "Cash deposits in the last 7 days, including this one"),
    "cash_credit_sum_7d": (
        "number",
        "Value of cash deposits in the last 7 days, including this one",
    ),
    "cash_near_threshold_count_7d": (
        "number",
        "Cash deposits in the last 7 days at 70-100% of the structuring threshold",
    ),
    "avg_debit_30d": ("number", "Average debit over the last 30 days, before this transaction"),
    "max_debit_30d": ("number", "Largest debit over the last 30 days, before this transaction"),
    "history_count_30d": ("number", "Transactions in the last 30 days, before this one"),
    "amount_to_avg_debit": ("number", "This amount divided by the 30-day average debit"),
    "new_counterparty": ("bool", "The account has not dealt with this counterparty before"),
    "new_counterparties_1h": ("number", "First-time counterparties paid in the last hour"),
    "distinct_credit_sources_24h": (
        "number",
        "Different payers into the account in the last 24 hours",
    ),
    "minutes_since_last_credit": ("number", "Minutes since the most recent credit"),
    "last_credit_amount": ("number", "Amount of the most recent credit"),
    "credit_sum_30m": ("number", "Value of credits in the 30 minutes before this transaction"),
    "pass_through_ratio": (
        "number",
        "This debit divided by credits received in the last 30 minutes",
    ),
    "outflow_ratio_48h": ("number", "Debits divided by credits over the last 48 hours"),
    "drain_ratio_1h": ("number", "Share of the balance an hour ago that has since been paid out"),
    "minutes_since_loan_credit": ("number", "Minutes since a loan was disbursed into the account"),
    "loan_credit_amount": ("number", "Amount of that loan disbursement"),
    "new_device": ("bool", "A device the account has not used before, when it has used others"),
    "device_age_minutes": ("number", "Minutes since this device was first seen on the account"),
    "other_devices": ("number", "Other devices the account has used before or besides this one"),
    "device_accounts": ("number", "Number of accounts this device has been used on"),
    "new_ip": (
        "bool",
        "A network address the account has not used before, when it has used others",
    ),
}
PARAMS: dict[str, tuple[str, str]] = {
    "structuring_threshold": ("number", "Cash amount that deposits may be kept under (rupees)"),
    "large_amount": ("number", "Amount treated as large for a single payment (rupees)"),
    "pass_through_floor": ("number", "Smallest credit considered for pass-through (rupees)"),
}
DEFAULT_PARAMS: dict[str, float] = {
    "structuring_threshold": 50_000.0,
    "large_amount": 20_000.0,
    "pass_through_floor": 5_000.0,
}


def fields_of(txn: TxnView) -> dict[str, Any]:
    hour = txn.ts.astimezone(IST).hour
    return {
        "amount": txn.amount,
        "channel": txn.channel,
        "direction": txn.direction,
        "status": txn.status,
        "purpose": txn.purpose,
        "hour": hour,
        "is_night": hour < 5,
        "is_transfer": txn.channel in TRANSFER_CHANNELS,
        "has_device": txn.device is not None,
        "has_counterparty": txn.counterparty is not None,
        "balance_after": txn.balance_after,
    }


def compute(
    txn: TxnView, snap: Snapshot, profile: AccountProfile, params: dict[str, float]
) -> dict[str, Any]:
    """All features for one transaction. Pure: the same inputs always give the same values."""
    now = txn.ts.timestamp()
    debit = txn.direction == "debit"
    live = txn.status != "failed"
    threshold = params.get("structuring_threshold", DEFAULT_PARAMS["structuring_threshold"])

    def near(amount: float) -> bool:
        return 0.7 * threshold <= amount < threshold

    f: dict[str, Any] = {
        "txn_count_1h": 1,
        "txn_count_24h": 1,
        "debit_count_1h": int(debit and live),
        "debit_sum_1h": txn.amount if debit and live else 0.0,
        "debit_sum_24h": txn.amount if debit and live else 0.0,
        "debit_sum_48h": txn.amount if debit and live else 0.0,
        "credit_sum_24h": txn.amount if not debit and live else 0.0,
        "credit_sum_48h": txn.amount if not debit and live else 0.0,
        "failed_count_1h": int(not live),
        "cash_credit_count_7d": 0,
        "cash_credit_sum_7d": 0.0,
        "cash_near_threshold_count_7d": 0,
    }
    if txn.channel == "cash" and not debit and live:
        f["cash_credit_count_7d"], f["cash_credit_sum_7d"] = 1, txn.amount
        f["cash_near_threshold_count_7d"] = int(near(txn.amount))
    debit_total, debit_count, debit_max, history_30d = 0.0, 0, 0.0, 0
    credit_30m = 0.0
    last_credit: Event | None = None
    loan_credit: Event | None = None
    sources: set[str] = set()
    paid_1h: list[str] = []
    credit_1h = 0.0
    if not debit and live and txn.counterparty:
        sources.add(txn.counterparty)
    for event in snap.history:
        age = now - event.ts
        if age < 0 or age > HISTORY_DAYS * 86_400:
            continue
        history_30d += 1
        if age <= 3_600:
            f["txn_count_1h"] += 1
            if not event.completed:
                f["failed_count_1h"] += 1
        if age <= 86_400:
            f["txn_count_24h"] += 1
        if not event.completed:
            continue
        if event.credit:
            # History is usually newest first, but late-arriving events can break the order.
            if last_credit is None or event.ts > last_credit.ts:
                last_credit = event
            if event.purpose == "loan_disbursement" and (
                loan_credit is None or event.ts > loan_credit.ts
            ):
                loan_credit = event
            if age <= 1_800:
                credit_30m += event.amount
            if age <= 3_600:
                credit_1h += event.amount
            if age <= 86_400:
                f["credit_sum_24h"] += event.amount
                if event.counterparty:
                    sources.add(event.counterparty)
            if age <= 172_800:
                f["credit_sum_48h"] += event.amount
            if event.channel == "cash" and age <= 7 * 86_400:
                f["cash_credit_count_7d"] += 1
                f["cash_credit_sum_7d"] += event.amount
                f["cash_near_threshold_count_7d"] += int(near(event.amount))
        else:
            debit_total += event.amount
            debit_count += 1
            debit_max = max(debit_max, event.amount)
            if age <= 3_600:
                f["debit_count_1h"] += 1
                f["debit_sum_1h"] += event.amount
                if event.counterparty:
                    paid_1h.append(event.counterparty)
            if age <= 86_400:
                f["debit_sum_24h"] += event.amount
            if age <= 172_800:
                f["debit_sum_48h"] += event.amount

    average = debit_total / debit_count if debit_count else None
    f["avg_debit_30d"] = average
    f["max_debit_30d"] = debit_max if debit_count else None
    f["history_count_30d"] = history_30d
    f["amount_to_avg_debit"] = txn.amount / average if average else None
    f["distinct_credit_sources_24h"] = len(sources)
    f["minutes_since_last_credit"] = (now - last_credit.ts) / 60 if last_credit else None
    f["last_credit_amount"] = last_credit.amount if last_credit else None
    f["credit_sum_30m"] = credit_30m
    f["pass_through_ratio"] = txn.amount / credit_30m if debit and credit_30m > 0 else None
    f["outflow_ratio_48h"] = (
        f["debit_sum_48h"] / f["credit_sum_48h"] if f["credit_sum_48h"] > 0 else None
    )
    f["minutes_since_loan_credit"] = (now - loan_credit.ts) / 60 if loan_credit else None
    f["loan_credit_amount"] = loan_credit.amount if loan_credit else None

    # Balance an hour ago = balance now + debits since - credits since.
    if txn.balance_after is not None and debit and live:
        before = txn.balance_after + f["debit_sum_1h"] - credit_1h
        f["drain_ratio_1h"] = f["debit_sum_1h"] / before if before > 0 else None
    else:
        f["drain_ratio_1h"] = None

    f["new_counterparty"] = bool(txn.counterparty) and not snap.counterparty_known
    # Earlier payees in the last hour were new if this is the first time each appears.
    seen_before = {e.counterparty for e in snap.history if now - e.ts > 3_600 and e.counterparty}
    new_recent = {c for c in paid_1h if c not in seen_before}
    if debit and f["new_counterparty"] and txn.counterparty:
        new_recent.add(txn.counterparty)
    f["new_counterparties_1h"] = len(new_recent)

    has_device = txn.device is not None
    first_seen = snap.device_first_seen
    f["new_device"] = has_device and first_seen is None and snap.known_devices > 0
    f["device_age_minutes"] = (
        None if not has_device else 0.0 if first_seen is None else max(0.0, (now - first_seen) / 60)
    )
    f["other_devices"] = (
        (snap.known_devices - (0 if first_seen is None else 1)) if has_device else None
    )
    f["device_accounts"] = snap.device_accounts if has_device else None
    f["new_ip"] = txn.ip is not None and not snap.ip_known and snap.known_ips > 0

    opened = profile.opened_on
    f["account_age_days"] = max(0.0, (txn.ts - opened).total_seconds() / 86_400) if opened else None
    f["account_product"] = profile.product
    f["party_type"] = profile.party_type
    f["kyc_risk"] = profile.kyc_risk
    return f
