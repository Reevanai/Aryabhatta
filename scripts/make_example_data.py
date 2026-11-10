"""Writes the small synthetic data set in examples/.

Everything is invented: names come from a fixed list of made-up given and family names,
identifiers are generated, and phone numbers, emails and IP addresses use reserved or
obviously fictional values. A few rows are deliberately invalid to show quarantine.

Phase 3 replaces this with the full generator (labelled fraud scenarios, NBFC data).
"""

import csv
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "examples"
GIVEN = [
    "Asha",
    "Bimal",
    "Chitra",
    "Dev",
    "Esha",
    "Farid",
    "Gita",
    "Hari",
    "Indu",
    "Jai",
    "Kavi",
    "Lata",
    "Mani",
    "Nila",
    "Om",
    "Pari",
    "Ravi",
    "Sita",
    "Tara",
    "Uma",
]
FAMILY = [
    "Testwala",
    "Samplekar",
    "Demoji",
    "Mockani",
    "Fakepur",
    "Dummyan",
    "Trialkar",
    "Examplan",
    "Placeholderi",
    "Syntheta",
]
CHANNELS = ["upi", "upi", "upi", "imps", "neft", "card", "cash", "aeps"]
BRANCHES = ["BR001", "BR002"]
PARTIES, ACCOUNTS_EXTRA, TRANSACTIONS = 200, 60, 2000


def main() -> None:
    rng = random.Random(20261110)
    OUT.mkdir(exist_ok=True)
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"

    parties = []
    for n in range(1, PARTIES + 1):
        organisation = n % 17 == 0
        name = (
            f"{rng.choice(FAMILY)} Traders {n}"
            if organisation
            else f"{rng.choice(GIVEN)} {rng.choice(FAMILY)}"
        )
        parties.append(
            {
                "customer_id": f"CUST{n:06d}",
                "party_type": "organisation" if organisation else "individual",
                "full_name": name,
                "pan": "".join(rng.choice(letters) for _ in range(5))
                + f"{n:04d}"
                + rng.choice(letters),
                "phone": f"9{rng.randrange(10**8, 10**9)}0"[:10] if n % 9 else "",
                "email": f"customer{n}@example.invalid" if n % 4 else "",
                "address": f"{n} Sample Road, Ward {n % 12 + 1}, Testnagar",
                "date_of_birth": (datetime(1960, 1, 1) + timedelta(days=rng.randrange(0, 16000)))
                .date()
                .isoformat(),
                "kyc_risk_category": rng.choices(["low", "medium", "high"], [80, 15, 5])[0],
                "onboarded_on": (now - timedelta(days=rng.randrange(30, 3000))).date().isoformat(),
            }
        )
    # Deliberately invalid rows.
    parties[10]["pan"] = "BADPAN"
    parties[55]["phone"] = "12345"
    parties[120]["party_type"] = "robot"
    _write("parties.csv", parties)

    accounts = []
    for n in range(1, PARTIES + ACCOUNTS_EXTRA + 1):
        owner = n if n <= PARTIES else rng.randrange(1, PARTIES + 1)
        accounts.append(
            {
                "account_number": f"5010{n:010d}",
                "customer_id": f"CUST{owner:06d}",
                "branch_code": BRANCHES[n % 2],
                "product_type": rng.choices(
                    ["savings", "current", "term_deposit", "loan"], [70, 15, 10, 5]
                )[0],
                "status": rng.choices(["active", "dormant", "closed"], [90, 8, 2])[0],
                "opened_on": (now - timedelta(days=rng.randrange(10, 3000))).date().isoformat(),
                "currency": "INR",
            }
        )
    accounts[5]["branch_code"] = "BR999"  # unknown branch
    accounts[40]["customer_id"] = "CUST999999"  # unknown customer
    _write("accounts.csv", accounts)

    transactions = []
    for n in range(1, TRANSACTIONS + 1):
        channel = rng.choice(CHANNELS)
        account = rng.randrange(1, PARTIES + ACCOUNTS_EXTRA + 1)
        row = {
            "source_reference": f"EX{n:010d}",
            "channel": channel,
            "direction": rng.choice(["debit", "credit"]),
            "amount": f"{rng.choice([50, 120, 499, 1500, 2500, 9999, 25000, 49000]) + rng.randrange(0, 100) / 100:.2f}",
            "currency": "INR",
            "booked_at": (now - timedelta(minutes=rng.randrange(0, 60 * 24 * 45))).isoformat(),
            "account_number": f"5010{account:010d}",
            "branch_code": "",
            "counterparty_account_number": f"7020{rng.randrange(10**9):010d}"
            if channel in {"imps", "neft"}
            else "",
            "counterparty_vpa": f"payee{rng.randrange(500)}@demobank" if channel == "upi" else "",
            "counterparty_bank_code": f"DEMO{rng.randrange(1, 30):03d}"
            if channel in {"imps", "neft", "upi"}
            else "",
            "device_id": f"device-{rng.randrange(300):04d}" if channel in {"upi", "card"} else "",
            "ip_address": f"203.0.113.{rng.randrange(1, 255)}" if channel == "upi" else "",
            "merchant_category": str(rng.choice([5411, 5812, 4900, 6011]))
            if channel == "card"
            else "",
            "status": rng.choices(["completed", "failed", "reversed"], [94, 5, 1])[0],
        }
        transactions.append(row)
    transactions[7]["amount"] = "-10.00"
    transactions[99]["account_number"] = "5010999999999999"  # unknown account
    transactions[500]["booked_at"] = "2026-13-45T00:00:00"
    _write("transactions.csv", transactions)


def _write(name: str, rows: list[dict[str, str]]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
