"""Writes a generated world as CSV files, labels and a manifest."""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sentinel_synth.generator import GENERATOR_VERSION
from sentinel_synth.world import VICTIM_ROLES, World

DATASETS = ("branches", "parties", "accounts", "loans", "devices", "transactions")
# Order in which the data files must be loaded.
LOAD_ORDER = ("parties", "accounts", "loans", "devices", "transactions")


def _rupees(paise: int) -> str:
    return f"{paise / 100:.2f}"


def rows(world: World) -> dict[str, list[dict[str, Any]]]:
    """Every data set as a list of rows, in the column order of the ingestion formats."""
    owner = {number: world.parties[a.party].customer_id for number, a in world.accounts.items()}
    return {
        "branches": [{"code": b.code, "name": b.name} for b in world.branches],
        "parties": [
            {
                "customer_id": p.customer_id,
                "party_type": p.kind,
                "full_name": p.name,
                "pan": p.pan,
                "phone": p.phone,
                "email": p.email,
                "address": p.address,
                "date_of_birth": p.dob.isoformat(),
                "kyc_risk_category": p.risk,
                "onboarded_on": p.onboarded.isoformat(),
            }
            for p in world.parties
        ],
        "accounts": [
            {
                "account_number": a.number,
                "customer_id": owner[a.number],
                "branch_code": world.branches[a.branch].code,
                "product_type": a.product,
                "status": a.status,
                "opened_on": a.opened.isoformat(),
                "currency": "INR",
            }
            for a in world.accounts.values()
        ],
        "loans": [
            {
                "loan_id": loan.loan_id,
                "customer_id": loan.customer_id,
                "account_number": loan.account_number,
                "product": loan.product,
                "principal": f"{loan.principal:.2f}",
                "annual_interest_rate": f"{loan.annual_rate * 100:.2f}",
                "tenure_months": loan.tenure_months,
                "disbursed_on": loan.disbursed_on.isoformat(),
                "emi_amount": f"{loan.emi:.2f}",
                "status": loan.status,
                "days_past_due": loan.days_past_due,
                "currency": "INR",
            }
            for loan in world.loans
        ],
        "devices": [
            {
                "device_id": d.device_id,
                "customer_id": d.customer_id,
                "platform": d.platform,
                "os_version": d.os_version,
                "rooted": str(d.rooted).lower(),
                "first_seen": world.as_utc(d.first_seen),
            }
            for d in world.devices
        ],
        "transactions": [
            {
                "source_reference": t.ref,
                "channel": t.channel,
                "direction": t.direction,
                "amount": _rupees(t.amount),
                "currency": "INR",
                "booked_at": world.as_utc(t.ts),
                "account_number": t.account,
                "branch_code": t.branch_code or "",
                "counterparty_account_number": t.cp_account or "",
                "counterparty_vpa": t.cp_vpa or "",
                "counterparty_bank_code": t.cp_bank or "",
                "device_id": t.device or "",
                "ip_address": t.ip or "",
                "merchant_category": t.mcc or "",
                "status": t.status,
                "attributes": json.dumps(t.attrs, sort_keys=True) if t.attrs else "",
            }
            for t in world.txns
        ],
    }


def labels(world: World) -> dict[str, list[dict[str, Any]]]:
    """Ground truth. Kept apart from the data files: it must never reach a detector as input."""
    by_id = {s.scenario_id: s for s in world.scenarios}
    return {
        "transactions": [
            {
                "source_reference": t.ref,
                "scenario_id": scenario_id,
                "scenario_type": by_id[scenario_id].type,
                "role": role,
                "is_fraud": str(by_id[scenario_id].is_fraud).lower(),
            }
            for t in world.txns
            for scenario_id, role in t.labels
        ],
        "parties": [
            {
                "customer_id": customer_id,
                "scenario_id": s.scenario_id,
                "scenario_type": s.type,
                "role": role,
                "is_fraud": str(s.is_fraud).lower(),
                "is_perpetrator": str(s.is_fraud and role not in VICTIM_ROLES).lower(),
            }
            for s in world.scenarios
            for customer_id, role in s.parties.items()
        ],
        "accounts": [
            {
                "account_number": number,
                "scenario_id": s.scenario_id,
                "scenario_type": s.type,
                "role": role,
                "is_fraud": str(s.is_fraud).lower(),
            }
            for s in world.scenarios
            for number, role in s.accounts.items()
        ],
    }


def stats(world: World) -> dict[str, Any]:
    fraud_ids = {s.scenario_id for s in world.scenarios if s.is_fraud}
    fraud_txns = sum(1 for t in world.txns if any(sid in fraud_ids for sid, _ in t.labels))
    scenario_counts = Counter(s.type for s in world.scenarios)
    return {
        "branches": len(world.branches),
        "parties": len(world.parties),
        "accounts": len(world.accounts),
        "loans": len(world.loans),
        "device_links": len(world.devices),
        "transactions": len(world.txns),
        "first_transaction": world.as_utc(world.txns[0].ts) if world.txns else None,
        "last_transaction": world.as_utc(world.txns[-1].ts) if world.txns else None,
        "transactions_by_channel": dict(sorted(Counter(t.channel for t in world.txns).items())),
        "transactions_by_status": dict(sorted(Counter(t.status for t in world.txns).items())),
        "fraud_transactions": fraud_txns,
        "fraud_transaction_rate": round(fraud_txns / len(world.txns), 6) if world.txns else 0,
        "scenarios": dict(sorted(scenario_counts.items())),
        "fraud_scenarios": len(fraud_ids),
        "lookalike_scenarios": len(world.scenarios) - len(fraud_ids),
    }


def _write_csv(path: Path, data: list[dict[str, Any]], columns: list[str]) -> str:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(world: World, out: Path) -> dict[str, Any]:
    """Write everything under `out` and return the manifest."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(exist_ok=True)
    data, truth = rows(world), labels(world)
    files: dict[str, dict[str, Any]] = {}
    for name in DATASETS:
        columns = list(data[name][0]) if data[name] else []
        digest = _write_csv(out / f"{name}.csv", data[name], columns)
        files[f"{name}.csv"] = {"rows": len(data[name]), "sha256": digest}
    label_columns = {
        "transactions": ["source_reference", "scenario_id", "scenario_type", "role", "is_fraud"],
        "parties": [
            "customer_id",
            "scenario_id",
            "scenario_type",
            "role",
            "is_fraud",
            "is_perpetrator",
        ],
        "accounts": ["account_number", "scenario_id", "scenario_type", "role", "is_fraud"],
    }
    for name, columns in label_columns.items():
        digest = _write_csv(out / "labels" / f"{name}.csv", truth[name], columns)
        files[f"labels/{name}.csv"] = {"rows": len(truth[name]), "sha256": digest}
    scenarios = [
        {
            "scenario_id": s.scenario_id,
            "type": s.type,
            "is_fraud": s.is_fraud,
            "description": s.description,
            "start": world.as_utc(s.start),
            "end": world.as_utc(s.end),
            "parties": s.parties,
            "accounts": s.accounts,
            "details": s.details,
        }
        for s in world.scenarios
    ]
    (out / "labels" / "scenarios.json").write_text(
        json.dumps(scenarios, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "notice": "Synthetic data. No real person, account or transaction is represented.",
        "generator_version": GENERATOR_VERSION,
        "config": world.config.model_dump(mode="json"),
        "load_order": list(LOAD_ORDER),
        "stats": stats(world),
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
