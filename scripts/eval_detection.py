"""Measures the starter rule pack against the labels of a generated data set.

    python scripts/eval_detection.py var/synth/demo

Replays the transactions through the same feature and scoring code the live scorer uses and
reports precision and recall. Runs on the generator's files directly, so it needs no database.
"""

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from sentinel_txn_fraud.public import STARTER_PACK, AccountProfile, Thresholds, TxnView, replay


def load(
    directory: Path,
) -> tuple[list[TxnView], dict[str, AccountProfile], dict[str, str], dict[str, set[str]]]:
    with (directory / "parties.csv").open(encoding="utf-8") as handle:
        parties = {p["customer_id"]: p for p in csv.DictReader(handle)}
    profiles = {}
    with (directory / "accounts.csv").open(encoding="utf-8") as handle:
        for a in csv.DictReader(handle):
            party = parties[a["customer_id"]]
            profiles[a["account_number"]] = AccountProfile(
                datetime.fromisoformat(a["opened_on"] + "T00:00:00+05:30"),
                a["product_type"],
                party["kyc_risk_category"] or None,
                party["party_type"],
            )
    txns = []
    with (directory / "transactions.csv").open(encoding="utf-8") as handle:
        for t in csv.DictReader(handle):
            attrs = json.loads(t["attributes"]) if t["attributes"] else {}
            balance = attrs.get("balance_after")
            txns.append(
                TxnView(
                    ts=datetime.fromisoformat(t["booked_at"]),
                    amount=float(t["amount"]),
                    channel=t["channel"],
                    direction=t["direction"],
                    status=t["status"],
                    account=t["account_number"],
                    counterparty=t["counterparty_account_number"] or t["counterparty_vpa"] or None,
                    device=t["device_id"] or None,
                    ip=t["ip_address"] or None,
                    purpose=attrs.get("purpose"),
                    balance_after=float(balance) if balance else None,
                    reference=t["source_reference"],
                )
            )
    fraud: dict[str, str] = {}
    scenarios: dict[str, set[str]] = {}
    with (directory / "labels" / "transactions.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["is_fraud"] == "true":
                fraud[row["source_reference"]] = row["scenario_type"]
                scenarios.setdefault(row["scenario_id"] + "|" + row["scenario_type"], set()).add(
                    row["source_reference"]
                )
    return txns, profiles, fraud, scenarios


def pct(value: float | None) -> str:
    return "   n/a" if value is None else f"{value:6.1%}"


def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "var/synth/demo")
    txns, profiles, fraud, scenarios = load(directory)
    started = time.perf_counter()
    result = replay(txns, STARTER_PACK, Thresholds(), profiles, fraud=fraud)
    elapsed = time.perf_counter() - started
    o = result.overall
    print(f"{o.evaluated} transactions replayed in {elapsed:.1f}s ({o.evaluated / elapsed:,.0f}/s)")
    print(
        f"flagged {o.flagged} ({result.alerts_per_day:.1f}/day)  precision {pct(o.precision)}  "
        f"recall {pct(o.recall)}  ({o.true_positives} of {o.labelled} labelled fraud transactions)"
    )
    print(f"\n{'rule':10s} {'hits':>6s} {'fraud':>6s} {'precision':>10s}")
    for code, m in result.by_rule.items():
        print(f"{code:10s} {m.flagged:6d} {m.true_positives:6d} {pct(m.precision):>10s}")
    print(f"\n{'scenario type':20s} {'txn recall':>10s} {'scenarios caught':>17s}")
    for kind in sorted(result.by_group):
        m = result.by_group[kind]
        mine = [refs for key, refs in scenarios.items() if key.endswith("|" + kind)]
        caught = sum(1 for refs in mine if refs & result.flagged_refs)
        print(f"{kind:20s} {pct(m.recall):>10s} {caught:>9d} of {len(mine):<4d}")


if __name__ == "__main__":
    main()
