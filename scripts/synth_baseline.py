"""Measures a few deliberately naive rules against the labels of a generated data set.

    python scripts/synth_baseline.py var/synth/demo

This is not the detection engine (that is Phase 4). It answers one question about the data:
is the injected fraud findable but not trivially separable from ordinary activity? A rule that
scored 100% precision and recall would mean the data is too easy to be a useful test.
"""

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def load(directory: Path) -> tuple[list[dict[str, Any]], dict[str, set[str]], int]:
    with (directory / "transactions.csv").open(encoding="utf-8") as handle:
        txns = list(csv.DictReader(handle))
    for txn in txns:
        txn["ts"] = datetime.fromisoformat(txn["booked_at"])
        txn["value"] = float(txn["amount"])
    fraud: dict[str, set[str]] = defaultdict(set)
    with (directory / "labels" / "transactions.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["is_fraud"] == "true":
                fraud[row["scenario_type"]].add(row["source_reference"])
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return txns, fraud, manifest["config"]["structuring_threshold"]


def near_threshold_cash(txns: list[dict[str, Any]], threshold: int) -> set[str]:
    """Cash deposits between 70% and 100% of the threshold."""
    return {
        t["source_reference"]
        for t in txns
        if t["channel"] == "cash"
        and t["direction"] == "credit"
        and 0.7 * threshold <= t["value"] < threshold
    }


def rapid_pass_through(txns: list[dict[str, Any]]) -> set[str]:
    """A debit within 30 minutes of a credit of at least 5,000, for 80-100% of its amount."""
    recent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hits: set[str] = set()
    for t in txns:
        if t["status"] != "completed":
            continue
        account = t["account_number"]
        if t["direction"] == "credit":
            if t["value"] >= 5_000:
                recent[account].append(t)
            continue
        window = [c for c in recent[account] if t["ts"] - c["ts"] <= timedelta(minutes=30)]
        recent[account] = window
        for credit in window:
            if 0.8 * credit["value"] <= t["value"] <= credit["value"]:
                hits.update({t["source_reference"], credit["source_reference"]})
    return hits


def new_device_at_night(txns: list[dict[str, Any]]) -> set[str]:
    """A payment between midnight and 05:00 IST from a device the account has not used before."""
    seen: dict[str, set[str]] = defaultdict(set)
    hits: set[str] = set()
    fresh: dict[tuple[str, str], datetime] = {}
    ist = timedelta(hours=5, minutes=30)
    for t in txns:
        device = t["device_id"]
        if not device or t["direction"] != "debit":
            continue
        account = t["account_number"]
        if seen[account] and device not in seen[account]:
            fresh.setdefault((account, device), t["ts"])
        started = fresh.get((account, device))
        if started and t["ts"] - started <= timedelta(hours=2) and (t["ts"] + ist).hour < 5:
            hits.add(t["source_reference"])
        seen[account].add(device)
    return hits


def score(name: str, flagged: set[str], targets: set[str], all_fraud: set[str]) -> dict[str, Any]:
    true_positive = len(flagged & targets)
    return {
        "rule": name,
        "flagged": len(flagged),
        "recall_on_target": round(true_positive / len(targets), 3) if targets else None,
        "precision_any_fraud": round(len(flagged & all_fraud) / len(flagged), 3)
        if flagged
        else None,
    }


def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "var/synth/demo")
    txns, fraud, threshold = load(directory)
    everything = set().union(*fraud.values())
    results = [
        score(
            "near-threshold cash deposit",
            near_threshold_cash(txns, threshold),
            fraud["structuring"],
            everything,
        ),
        score(
            "rapid pass-through (30 min)", rapid_pass_through(txns), fraud["mule_chain"], everything
        ),
        score(
            "new device at night", new_device_at_night(txns), fraud["account_takeover"], everything
        ),
    ]
    print(
        f"{len(txns)} transactions, {len(everything)} labelled as fraud ({len(everything) / len(txns):.2%})"
    )
    print(
        f"{'rule':30s} {'flagged':>8s} {'recall on its target':>21s} {'precision (any fraud)':>22s}"
    )
    for r in results:
        print(
            f"{r['rule']:30s} {r['flagged']:8d} {r['recall_on_target']!s:>21s} {r['precision_any_fraud']!s:>22s}"
        )


if __name__ == "__main__":
    main()
