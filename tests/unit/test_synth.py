"""The synthetic data generator: validity, determinism, and the shape of each scenario."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sentinel_ingestion.public import AccountRow, DeviceRow, LoanRow, PartyRow, transaction_from_csv
from sentinel_synth.cli import main as cli_main
from sentinel_synth.public import (
    LOAD_ORDER,
    SCENARIO_TYPES,
    World,
    generate,
    labels,
    profile,
    rows,
    stats,
    write,
)

BANK_ONLY = {
    "mule_chain",
    "layering",
    "circular_flow",
    "structuring",
    "account_takeover",
    "app_scam",
}


@pytest.fixture(scope="module")
def world() -> World:
    return generate(profile("tiny"))


@pytest.fixture(scope="module")
def data(world: World) -> dict[str, list[dict[str, Any]]]:
    return rows(world)


@pytest.fixture(scope="module")
def truth(world: World) -> dict[str, list[dict[str, Any]]]:
    return labels(world)


@pytest.fixture(scope="module")
def nbfc() -> World:
    return generate(profile("tiny", institution="nbfc"))


def _txns(world: World, scenario_id: str, role: str | None = None) -> list[Any]:
    return [
        t
        for t in world.txns
        if any(sid == scenario_id and (role is None or r == role) for sid, r in t.labels)
    ]


def _of_type(world: World, kind: str) -> list[Any]:
    found = [s for s in world.scenarios if s.type == kind]
    assert found, f"no {kind} scenario generated"
    return found


# Determinism ---------------------------------------------------------------------------


def _digest(world: World) -> str:
    payload = json.dumps([rows(world), labels(world)], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def test_same_seed_gives_identical_data(world: World) -> None:
    assert _digest(generate(profile("tiny"))) == _digest(world)


def test_different_seed_gives_different_data(world: World) -> None:
    assert _digest(generate(profile("tiny", seed=7))) != _digest(world)


# Validity against the ingestion formats -------------------------------------------------


def test_every_row_passes_ingestion_validation(data: dict[str, list[dict[str, Any]]]) -> None:
    for row in data["parties"]:
        PartyRow.model_validate(row)
    for row in data["accounts"]:
        AccountRow.model_validate(row)
    for row in data["loans"]:
        LoanRow.model_validate(row)
    for row in data["devices"]:
        DeviceRow.model_validate(row)
    for row in data["transactions"]:
        transaction_from_csv({k: str(v) for k, v in row.items()})


def test_nbfc_rows_pass_ingestion_validation(nbfc: World) -> None:
    data = rows(nbfc)
    for row in data["loans"]:
        LoanRow.model_validate(row)
    for row in data["transactions"]:
        transaction_from_csv({k: str(v) for k, v in row.items()})


def test_references_resolve(data: dict[str, list[dict[str, Any]]]) -> None:
    customers = {p["customer_id"] for p in data["parties"]}
    accounts = {a["account_number"]: a["customer_id"] for a in data["accounts"]}
    branches = {b["code"] for b in data["branches"]}
    assert len(customers) == len(data["parties"])
    assert len(accounts) == len(data["accounts"])
    assert {a["customer_id"] for a in data["accounts"]} <= customers
    assert {a["branch_code"] for a in data["accounts"]} <= branches
    assert {d["customer_id"] for d in data["devices"]} <= customers
    for loan in data["loans"]:
        assert accounts[loan["account_number"]] == loan["customer_id"]
    for txn in data["transactions"]:
        assert txn["account_number"] in accounts
        assert not txn["branch_code"] or txn["branch_code"] in branches
    refs = [t["source_reference"] for t in data["transactions"]]
    assert len(set(refs)) == len(refs)
    pairs = [(d["device_id"], d["customer_id"]) for d in data["devices"]]
    assert len(set(pairs)) == len(pairs)


def test_transactions_are_in_time_order_inside_the_window(world: World) -> None:
    stamps = [t.ts for t in world.txns]
    assert stamps == sorted(stamps)
    assert world.start <= stamps[0]
    assert stamps[-1] <= world.end


def test_internal_transfers_have_two_matching_legs(world: World) -> None:
    legs: dict[str, list[Any]] = defaultdict(list)
    for txn in world.txns:
        if "utr" in txn.attrs:
            legs[txn.attrs["utr"]].append(txn)
    assert len(legs) > 100
    for pair in legs.values():
        assert len(pair) == 2
        debit, credit = sorted(pair, key=lambda t: t.direction, reverse=True)
        assert (debit.direction, credit.direction) == ("debit", "credit")
        assert debit.amount == credit.amount
        assert debit.status == credit.status
        assert (debit.cp_account, credit.cp_account) == (credit.account, debit.account)
        assert (
            timedelta(0) <= credit.ts - debit.ts <= timedelta(seconds=20) or credit.ts == world.end
        )


def test_deposit_balances_never_go_negative(world: World) -> None:
    balance = {n: a.opening_balance for n, a in world.accounts.items()}
    for txn in world.txns:
        if txn.status != "completed":
            continue
        balance[txn.account] += txn.amount if txn.direction == "credit" else -txn.amount
        if world.accounts[txn.account].product not in {"loan", "overdraft"}:
            assert balance[txn.account] >= 0
            assert txn.attrs["balance_after"] == f"{balance[txn.account] / 100:.2f}"


def test_no_real_world_identifiers_are_used(data: dict[str, list[dict[str, Any]]]) -> None:
    for party in data["parties"]:
        assert not party["email"] or party["email"].endswith("@example.invalid")
        assert party["phone"].startswith("61000")
    for txn in data["transactions"]:
        # Every address is in the IPv6 documentation range.
        assert not txn["ip_address"] or txn["ip_address"].startswith("2001:db8:")
        assert not txn["counterparty_bank_code"] or txn["counterparty_bank_code"].startswith("XSYN")


# Realism ---------------------------------------------------------------------------------


def test_population_mix_and_channel_mix_are_plausible(world: World) -> None:
    segments = Counter(p.segment for p in world.parties)
    assert segments["salaried"] > segments["student"] > 0
    assert 0 < segments["organisation"] < len(world.parties) * 0.15
    channels = Counter(t.channel for t in world.txns)
    assert channels["upi"] > channels["card"] > 0
    assert channels["upi"] / len(world.txns) > 0.5
    assert all(channels[c] > 0 for c in ("imps", "neft", "cash", "aeps", "internal"))


def test_activity_follows_a_daytime_pattern(world: World) -> None:
    hours = Counter(t.ts.hour for t in world.txns if not t.labels)
    night = sum(hours[h] for h in range(0, 5))
    day = sum(hours[h] for h in range(9, 21))
    assert day > 20 * night


def test_some_transactions_fail(world: World) -> None:
    failed = sum(1 for t in world.txns if t.status == "failed") / len(world.txns)
    assert 0.005 < failed < 0.06


def test_salaried_customers_receive_a_monthly_salary(world: World) -> None:
    salaries = Counter(
        t.account
        for t in world.txns
        if t.attrs.get("purpose") == "salary" and t.direction == "credit"
    )
    salaried = [p for p in world.parties if p.segment == "salaried" and p.account in salaries]
    assert len(salaried) > 50
    assert all(1 <= salaries[p.account] <= 2 for p in salaried)


def test_fraud_is_rare(world: World) -> None:
    summary = stats(world)
    assert 0.001 < summary["fraud_transaction_rate"] < 0.05


# Labels ------------------------------------------------------------------------------------


def test_every_scenario_type_is_present(world: World) -> None:
    kinds = {s.type for s in world.scenarios}
    assert set(SCENARIO_TYPES) <= kinds
    assert sum(k.startswith("lookalike:") for k in kinds) == 5


def test_scenario_counts_match_the_configuration(world: World) -> None:
    counts = Counter(s.type for s in world.scenarios)
    for kind, wanted in world.config.scenarios.model_dump().items():
        assert counts[kind] == wanted
    assert sum(not s.is_fraud for s in world.scenarios) == world.config.hard_negatives


def test_labels_refer_to_real_records(
    data: dict[str, list[dict[str, Any]]], truth: dict[str, list[dict[str, Any]]]
) -> None:
    refs = {t["source_reference"] for t in data["transactions"]}
    customers = {p["customer_id"] for p in data["parties"]}
    accounts = {a["account_number"] for a in data["accounts"]}
    assert {row["source_reference"] for row in truth["transactions"]} <= refs
    assert {row["customer_id"] for row in truth["parties"]} <= customers
    assert {row["account_number"] for row in truth["accounts"]} <= accounts


def test_victims_are_not_marked_as_perpetrators(truth: dict[str, list[dict[str, Any]]]) -> None:
    victims = [r for r in truth["parties"] if r["role"] == "victim"]
    assert victims
    assert all(r["is_perpetrator"] == "false" and r["is_fraud"] == "true" for r in victims)
    assert all(r["is_perpetrator"] == "false" for r in truth["parties"] if r["is_fraud"] == "false")
    assert any(r["is_perpetrator"] == "true" for r in truth["parties"])


def test_every_scenario_has_transactions_inside_its_time_range(world: World) -> None:
    for scenario in world.scenarios:
        found = _txns(world, scenario.scenario_id)
        assert found, scenario.type
        assert scenario.start == min(t.ts for t in found)
        assert scenario.end == max(t.ts for t in found)


def test_labels_are_not_present_in_the_data_files(data: dict[str, list[dict[str, Any]]]) -> None:
    text = json.dumps(data["transactions"])
    for word in ("SCN-", "mule", "fraud", "victim", "scam", "lookalike"):
        assert word not in text


# Scenario structure ------------------------------------------------------------------------


def test_mule_chain_moves_money_quickly_along_a_path(world: World) -> None:
    for scenario in _of_type(world, "mule_chain"):
        chain = scenario.details["chain"]
        assert 2 <= len(chain) <= 4
        hops = [t for t in _txns(world, scenario.scenario_id, "mule_hop") if t.direction == "debit"]
        inbound = _txns(world, scenario.scenario_id, "mule_in")
        assert len(inbound) == scenario.details["victim_payments"]
        assert len(hops) == len(inbound) * (len(chain) - 1)
        account_owner = {n: world.parties[a.party].customer_id for n, a in world.accounts.items()}
        for hop in hops:
            position = chain.index(account_owner[hop.account])
            assert account_owner[hop.cp_account] == chain[position + 1]
        # Money leaves the first mule within half an hour of arriving.
        first = [h for h in hops if account_owner[h.account] == chain[0]]
        for arrival in inbound:
            assert any(
                timedelta(0) < h.ts - arrival.ts <= timedelta(minutes=31)
                and h.amount < arrival.amount
                for h in first
            )
        assert _txns(world, scenario.scenario_id, "cash_out")


def test_circular_flow_returns_to_its_start(world: World) -> None:
    for scenario in _of_type(world, "circular_flow"):
        members = scenario.details["members"]
        owner = {n: world.parties[a.party].customer_id for n, a in world.accounts.items()}
        hops = [
            t for t in _txns(world, scenario.scenario_id, "cycle_hop") if t.direction == "debit"
        ]
        assert len(hops) == len(members) * scenario.details["cycles"]
        for index, hop in enumerate(hops):
            assert owner[hop.account] == members[index % len(members)]
            assert owner[hop.cp_account] == members[(index + 1) % len(members)]
        amounts = [h.amount for h in hops]
        assert amounts == sorted(amounts, reverse=True)
        assert amounts[-1] > amounts[0] * 0.7


def test_structuring_keeps_each_deposit_under_the_threshold(world: World) -> None:
    threshold = world.config.structuring_threshold * 100
    for scenario in _of_type(world, "structuring"):
        deposits = _txns(world, scenario.scenario_id, "structured_deposit")
        assert len(deposits) == scenario.details["deposits"] >= 6
        assert all(t.channel == "cash" and t.direction == "credit" for t in deposits)
        assert all(threshold * 0.69 <= t.amount < threshold for t in deposits)
        assert sum(t.amount for t in deposits) > 3 * threshold
        assert max(t.ts for t in deposits) - min(t.ts for t in deposits) <= timedelta(days=16)
        assert all(10 <= t.ts.hour < 16 for t in deposits)


def test_account_takeover_uses_an_unfamiliar_device_and_network(world: World) -> None:
    for scenario in _of_type(world, "account_takeover"):
        (victim_id,) = [c for c, role in scenario.parties.items() if role == "victim"]
        victim = next(p for p in world.parties if p.customer_id == victim_id)
        transfers = _txns(world, scenario.scenario_id, "ato_transfer")
        device = scenario.details["attacker_device"]
        earlier = [
            t
            for t in world.txns
            if t.account == victim.account and t.ts < scenario.start and t.device
        ]
        assert device not in {t.device for t in earlier}
        assert all(t.device == device for t in transfers)
        assert all(not t.ip.startswith(victim.net_prefix) for t in transfers)
        assert scenario.end - scenario.start <= timedelta(hours=1, minutes=15)
        assert transfers[0].amount == 100  # one-rupee test payment
        assert len(transfers) == scenario.details["transfers"] + 1


def test_app_scam_payments_come_from_the_victims_own_device(world: World) -> None:
    for scenario in _of_type(world, "app_scam"):
        (victim_id,) = [c for c, role in scenario.parties.items() if role == "victim"]
        victim = next(p for p in world.parties if p.customer_id == victim_id)
        payments = [
            t
            for t in _txns(world, scenario.scenario_id, "victim_payment")
            if t.account == victim.account
        ]
        assert len(payments) == scenario.details["payments"]
        assert all(
            t.device == victim.devices[0] and t.ip.startswith(victim.net_prefix) for t in payments
        )
        amounts = [t.amount for t in payments]
        assert amounts == sorted(amounts)
        assert amounts[0] >= victim.monthly_income * 100 * 0.29


def test_layering_splits_then_exits(world: World) -> None:
    for scenario in _of_type(world, "layering"):
        (inflow,) = _txns(world, scenario.scenario_id, "layer_in")
        hops = [
            t for t in _txns(world, scenario.scenario_id, "layer_hop") if t.direction == "debit"
        ]
        exits = _txns(world, scenario.scenario_id, "layer_out")
        assert inflow.amount >= 8 * 10_000_000
        assert len(hops) >= scenario.details["layer1"]
        assert all(h.ts > inflow.ts for h in hops)
        assert 0.85 * inflow.amount < sum(t.amount for t in exits) < inflow.amount
        assert all(t.cp_bank.startswith("XSYN") for t in exits)


def _shared(scenario: Any) -> int:
    return sum(len(v) for v in scenario.details["shared"].values())


def test_fraud_ring_members_are_new_and_linked(world: World) -> None:
    for scenario in _of_type(world, "fraud_ring"):
        members = [p for p in world.parties if p.customer_id in scenario.parties]
        assert 5 <= len(members) <= 10
        assert all(p.onboarded >= (world.start - timedelta(days=40)).date() for p in members)
        assert _shared(scenario) >= 2
        lead = members[0]
        for customer_id in scenario.details["shared"]["device"]:
            other = next(p for p in members if p.customer_id == customer_id)
            assert lead.devices[0] in other.devices
        for customer_id in scenario.details["shared"]["phone"]:
            assert next(p for p in members if p.customer_id == customer_id).phone == lead.phone
        internal = [
            t for t in _txns(world, scenario.scenario_id, "ring_internal") if t.direction == "debit"
        ]
        accounts = {p.account for p in members}
        assert all(t.account in accounts and t.cp_account in accounts for t in internal)


def test_synthetic_identities_share_attributes_and_default(world: World) -> None:
    for scenario in _of_type(world, "synthetic_identity"):
        members = [p for p in world.parties if p.customer_id in scenario.parties]
        assert len({p.dob for p in members}) < len(members) or len(members) < 3
        assert len({p.name.split()[-1] for p in members}) <= 2
        assert _shared(scenario) >= 1
        loans = [loan for loan in world.loans if loan.loan_id in scenario.details["loans"]]
        assert len(loans) == len(members)
        assert _txns(world, scenario.scenario_id, "loan_disbursement")


def test_loan_fraud_is_drawn_down_at_once_and_never_repaid(world: World) -> None:
    for scenario in _of_type(world, "loan_fraud"):
        for loan_id in scenario.details["loans"]:
            loan = next(x for x in world.loans if x.loan_id == loan_id)
            disbursed = [
                t
                for t in _txns(world, scenario.scenario_id, "loan_disbursement")
                if t.account == loan.account_number
            ]
            assert len(disbursed) == 1
            assert disbursed[0].amount == loan.principal * 100
            repayments = [
                t
                for t in world.txns
                if t.account == loan.account_number
                and t.direction == "credit"
                and t.status == "completed"
            ]
            assert repayments == []
            missed = [
                t
                for t in _txns(world, scenario.scenario_id, "missed_emi")
                if t.account == loan.account_number
            ]
            assert (loan.status == "overdue") == bool(missed)
            assert (loan.days_past_due > 0) == bool(missed)
        outflow = sum(t.amount for t in _txns(world, scenario.scenario_id, "bust_out"))
        principal = (
            sum(x.principal for x in world.loans if x.loan_id in scenario.details["loans"]) * 100
        )
        assert 0.8 * principal <= outflow <= principal


def test_lookalikes_are_labelled_legitimate(
    world: World, truth: dict[str, list[dict[str, Any]]]
) -> None:
    lookalikes = [s for s in world.scenarios if not s.is_fraud]
    assert {s.type for s in lookalikes} == {
        "lookalike:pass_through",
        "lookalike:new_device",
        "lookalike:large_payment",
        "lookalike:cash_heavy",
        "lookalike:round_trip",
    }
    ids = {s.scenario_id for s in lookalikes}
    assert all(r["is_fraud"] == "false" for r in truth["transactions"] if r["scenario_id"] in ids)


def test_a_customer_is_in_at_most_one_scenario_except_shared_scam_mules(world: World) -> None:
    seen: dict[str, list[str]] = defaultdict(list)
    for scenario in world.scenarios:
        for customer_id in scenario.parties:
            seen[customer_id].append(scenario.type)
    for kinds in seen.values():
        assert len(kinds) == 1 or set(kinds) == {"app_scam"}


# NBFC ---------------------------------------------------------------------------------------


def test_nbfc_has_only_loan_accounts_and_applicable_scenarios(nbfc: World) -> None:
    assert {a.product for a in nbfc.accounts.values()} == {"loan"}
    kinds = {s.type for s in nbfc.scenarios}
    assert kinds == set(SCENARIO_TYPES) - BANK_ONLY
    assert len(nbfc.loans) == len(nbfc.accounts)
    channels = Counter(t.channel for t in nbfc.txns)
    assert set(channels) <= {"neft", "upi", "other"}
    purposes = Counter(t.attrs.get("purpose") for t in nbfc.txns)
    assert purposes["emi"] > purposes["loan_disbursement"] > 0


def test_nbfc_fraudulent_loans_default(nbfc: World) -> None:
    flagged = {
        number
        for s in nbfc.scenarios
        for number, role in s.accounts.items()
        if role == "fraudulent_loan"
    }
    assert flagged
    for loan in nbfc.loans:
        if loan.account_number in flagged:
            paid = [
                t
                for t in nbfc.txns
                if t.account == loan.account_number
                and t.direction == "credit"
                and t.status == "completed"
            ]
            assert paid == []


# Files and command line -----------------------------------------------------------------------


def test_written_files_match_their_manifest(world: World, tmp_path: Path) -> None:
    manifest = write(world, tmp_path)
    assert manifest["load_order"] == list(LOAD_ORDER)
    assert "Synthetic" in manifest["notice"]
    for name, info in manifest["files"].items():
        content = (tmp_path / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == info["sha256"]
        assert content.count(b"\n") - 1 == info["rows"]
    scenarios = json.loads((tmp_path / "labels" / "scenarios.json").read_text())
    assert len(scenarios) == len(world.scenarios)
    assert manifest["stats"]["transactions"] == len(world.txns)
    first = (tmp_path / "transactions.csv").read_text().splitlines()[1].split(",")
    assert Decimal(first[3]) > 0
    assert datetime.fromisoformat(first[5]).utcoffset() == timedelta(0)


def test_command_line_generates_and_describes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(
        [
            "generate",
            "--profile",
            "tiny",
            "--customers",
            "120",
            "--days",
            "20",
            "--seed",
            "3",
            "--out",
            str(tmp_path),
        ]
    )
    assert "fraud scenarios" in capsys.readouterr().out
    cli_main(["describe", str(tmp_path)])
    described = json.loads(capsys.readouterr().out)
    assert described["config"]["customers"] == 120
    assert described["stats"]["parties"] >= 120


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(KeyError):
        profile("enormous")


def test_too_few_customers_for_the_scenarios_fails_clearly() -> None:
    with pytest.raises(RuntimeError, match="increase `customers`"):
        generate(profile("small", customers=60, days=20))
