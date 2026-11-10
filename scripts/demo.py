"""Seed and demonstrate the Phase 2 platform core against the running development stack.

    python scripts/demo.py seed   # create branches and load the generated data set
    python scripts/demo.py demo   # data set summary, tokenised events, audited reveal, audit check

Uses the development-only client credentials from deploy/compose. Synthetic data only.
"""

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from aiokafka import AIOKafkaConsumer

API = os.environ.get("SENTINEL_API", "http://localhost:8000")
TOKEN_URL = os.environ.get(
    "SENTINEL_TOKEN_URL", "http://localhost:8080/realms/sentinel-demo/protocol/openid-connect/token"
)
KAFKA = os.environ.get("SENTINEL_KAFKA", "localhost:19092")
DATA = Path(os.environ.get("SENTINEL_SYNTH_DIR", "var/synth/demo"))
LOAD_ORDER = ("parties", "accounts", "loans", "devices", "transactions")
CLIENTS = {
    "cli": ("sentinel-demo-cli", "dev-demo-cli-secret"),
    "auditor": ("sentinel-demo-auditor", "dev-demo-auditor-secret"),
}


def token(client: str) -> dict[str, str]:
    client_id, secret = CLIENTS[client]
    response = httpx.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
        timeout=10,
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def show(title: str, value: Any) -> None:
    print(f"\n== {title}")
    print(json.dumps(value, indent=2, default=str) if not isinstance(value, str) else value)


def seed() -> None:
    if not (DATA / "manifest.json").exists():
        sys.exit(f"no data set in {DATA}; run `make seed`, which generates one first")
    headers = token("cli")
    with httpx.Client(base_url=API, headers=headers, timeout=120) as api:
        show("Tenant", api.get("/v1/tenant").raise_for_status().json())
        with (DATA / "branches.csv").open(encoding="utf-8") as handle:
            for branch in csv.DictReader(handle):
                created = api.post("/v1/branches", json=branch)
                if created.status_code not in {201, 409}:
                    created.raise_for_status()
        print()
        for dataset in LOAD_ORDER:
            started = time.perf_counter()
            with (DATA / f"{dataset}.csv").open("rb") as handle:
                job = (
                    api.post(
                        "/v1/ingestion/batches", data={"dataset": dataset}, files={"file": handle}
                    )
                    .raise_for_status()
                    .json()
                )
            while job["status"] in {"received", "validating", "loading"}:
                time.sleep(0.3)
                job = api.get(f"/v1/ingestion/batches/{job['id']}").raise_for_status().json()
            elapsed = time.perf_counter() - started
            print(
                f"{dataset:13s} {job['status']:22s} loaded={job['records_loaded']:7d} "
                f"quarantined={job['records_quarantined']:3d} in {elapsed:.1f}s"
            )
            if job["status"] == "failed":
                sys.exit(f"batch failed: {job['error']}")


def summary() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    stats = manifest["stats"]
    show(
        "Generated data set (synthetic)",
        {
            k: stats[k]
            for k in (
                "parties",
                "accounts",
                "loans",
                "device_links",
                "transactions",
                "fraud_transactions",
                "fraud_transaction_rate",
                "scenarios",
            )
        },
    )
    scenarios = json.loads((DATA / "labels" / "scenarios.json").read_text(encoding="utf-8"))
    example = next(s for s in scenarios if s["type"] == "mule_chain")
    show(
        "One labelled scenario (ground truth, kept out of the data files)",
        {k: example[k] for k in ("scenario_id", "type", "description", "start", "end", "details")},
    )


async def first_event(tenant_id: str) -> dict[str, Any]:
    """The first transaction event on the bus that belongs to the given tenant."""
    consumer = AIOKafkaConsumer(
        "sentinel.ingestion.transaction.v1",
        bootstrap_servers=KAFKA,
        group_id=None,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async with asyncio.timeout(30):
            async for record in consumer:
                if dict(record.headers).get("tenant_id") == tenant_id.encode():
                    event: dict[str, Any] = json.loads(record.value)
                    return event
    finally:
        await consumer.stop()
    raise RuntimeError("no event found")


def demo() -> None:
    summary()
    cli, auditor = token("cli"), token("auditor")
    with httpx.Client(base_url=API, timeout=30) as api:
        tenant_id = api.get("/v1/tenant", headers=cli).raise_for_status().json()["id"]
        event = asyncio.run(first_event(tenant_id))
        show("A transaction event on the bus (tokens only, no clear identifiers)", event)

        account_token = event["payload"]["account_token"]
        revealed = (
            api.post(
                "/v1/pii/reveal",
                headers=cli,
                json={
                    "token": account_token,
                    "purpose": "fraud_prevention",
                    "reason": "demo: confirm account",
                },
            )
            .raise_for_status()
            .json()
        )
        show("Reveal with an allowed purpose", revealed)

        denied = api.post(
            "/v1/pii/reveal",
            headers=cli,
            json={
                "token": account_token,
                "purpose": "service_operations",
                "reason": "demo: no real need",
            },
        )
        show(
            "Reveal with a purpose the field does not allow",
            f"HTTP {denied.status_code}: {denied.json()['detail']}",
        )

        blocked = api.post(
            "/v1/pii/reveal",
            headers=auditor,
            json={"token": account_token, "purpose": "fraud_prevention", "reason": "demo"},
        )
        show(
            "Reveal attempted by the auditor client, which lacks the role",
            f"HTTP {blocked.status_code}: {blocked.json()['detail']}",
        )

        events = (
            api.get(
                "/v1/audit/events", params={"resource": f"token:{account_token}"}, headers=auditor
            )
            .raise_for_status()
            .json()
        )
        show(
            "Audit trail of those two reveal attempts",
            [
                {k: e[k] for k in ("sequence", "action", "actor_id", "purpose", "details")}
                for e in events["items"]
            ],
        )
        show(
            "Audit chain verification",
            api.get("/v1/audit/verify", headers=auditor).raise_for_status().json(),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed", "demo"])
    args = parser.parse_args()
    seed() if args.command == "seed" else demo()


if __name__ == "__main__":
    main()
