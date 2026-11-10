"""Synthetic test records. Nothing here is, or is derived from, real data."""

from datetime import UTC, datetime, timedelta
from typing import Any

PARTY_HEADER = (
    "customer_id,party_type,full_name,pan,phone,email,address,date_of_birth,"
    "kyc_risk_category,onboarded_on"
)
ACCOUNT_HEADER = "account_number,customer_id,branch_code,product_type,status,opened_on,currency"
TXN_HEADER = (
    "source_reference,channel,direction,amount,currency,booked_at,account_number,branch_code,"
    "counterparty_account_number,counterparty_vpa,counterparty_bank_code,device_id,ip_address,"
    "merchant_category,status"
)


def party_row(n: int, **over: str) -> dict[str, str]:
    row = {
        "customer_id": f"CUST{n:05d}",
        "party_type": "individual",
        "full_name": f"Test Person {n}",
        "pan": f"ABCPT{n:04d}Z",
        "phone": f"90000{n:05d}",
        "email": f"person{n}@example.invalid",
        "address": f"{n} Sample Street, Testpur",
        "date_of_birth": "1990-01-15",
        "kyc_risk_category": "low",
        "onboarded_on": "2020-06-01",
    }
    row.update(over)
    return row


def account_row(n: int, branch: str = "BR001", **over: str) -> dict[str, str]:
    row = {
        "account_number": f"00112233{n:06d}",
        "customer_id": f"CUST{n:05d}",
        "branch_code": branch,
        "product_type": "savings",
        "status": "active",
        "opened_on": "2020-06-02",
        "currency": "INR",
    }
    row.update(over)
    return row


def txn(n: int, account: int = 1, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source_reference": f"TXN-{n:08d}",
        "channel": "upi",
        "direction": "debit",
        "amount": {"amount": "1500.00", "currency": "INR"},
        "booked_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "account_number": f"00112233{account:06d}",
        "counterparty_vpa": "merchant@examplebank",
        "device_id": f"device-{account}",
        "ip_address": "203.0.113.7",
        "status": "completed",
    }
    row.update(over)
    return row


def to_csv(header: str, rows: list[dict[str, str]]) -> bytes:
    columns = header.split(",")
    lines = [header] + [",".join(f'"{r.get(c, "")}"' for c in columns) for r in rows]
    return ("\n".join(lines) + "\n").encode()


def flat_txn(n: int, account: int = 1, **over: str) -> dict[str, str]:
    row = {
        "source_reference": f"TXN-{n:08d}",
        "channel": "imps",
        "direction": "credit",
        "amount": "2500.50",
        "currency": "INR",
        "booked_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        "account_number": f"00112233{account:06d}",
        "counterparty_account_number": "998877665544",
        "status": "completed",
    }
    row.update(over)
    return row


LOAN_HEADER = (
    "loan_id,customer_id,account_number,product,principal,annual_interest_rate,tenure_months,"
    "disbursed_on,emi_amount,status,days_past_due,currency"
)
DEVICE_HEADER = "device_id,customer_id,platform,os_version,rooted,first_seen"


def loan_row(n: int, **over: str) -> dict[str, str]:
    row = {
        "loan_id": f"LN{n:06d}",
        "customer_id": f"CUST{n:05d}",
        "account_number": f"00112233{n:06d}",
        "product": "personal",
        "principal": "150000.00",
        "annual_interest_rate": "14.50",
        "tenure_months": "24",
        "disbursed_on": "2025-03-10",
        "emi_amount": "7240.00",
        "status": "active",
        "days_past_due": "0",
        "currency": "INR",
    }
    row.update(over)
    return row


def device_row(n: int, customer: int | None = None, **over: str) -> dict[str, str]:
    row = {
        "device_id": f"device-{n}",
        "customer_id": f"CUST{(customer or n):05d}",
        "platform": "android",
        "os_version": "14",
        "rooted": "false",
        "first_seen": "2025-06-01T09:30:00+05:30",
    }
    row.update(over)
    return row
