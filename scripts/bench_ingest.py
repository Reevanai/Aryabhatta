"""Measures batch ingestion throughput against the running development stack.

    python scripts/bench_ingest.py [rows]

Uploads a generated synthetic transaction file for the demo tenant (run `make seed` first)
and reports rows per second from upload to completion: validation, tokenisation, vault
writes, inserts and outbox events.
"""

import csv
import io
import random
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
from demo import API, token  # type: ignore[import-not-found]


def build(rows: int, run_id: str) -> bytes:
    rng = random.Random(run_id)
    now = datetime.now(UTC)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "source_reference",
            "channel",
            "direction",
            "amount",
            "currency",
            "booked_at",
            "account_number",
            "counterparty_vpa",
            "device_id",
            "ip_address",
            "status",
        ]
    )
    for n in range(rows):
        writer.writerow(
            [
                f"BENCH-{run_id}-{n:08d}",
                "upi",
                rng.choice(["debit", "credit"]),
                f"{rng.randrange(1, 50000)}.{rng.randrange(100):02d}",
                "INR",
                (now - timedelta(seconds=rng.randrange(0, 86400 * 20))).isoformat(),
                f"5010{rng.randrange(1, 261):010d}",
                f"payee{rng.randrange(2000)}@demobank",
                f"device-{rng.randrange(500):04d}",
                f"203.0.113.{rng.randrange(1, 255)}",
                "completed",
            ]
        )
    return out.getvalue().encode()


def main() -> None:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    data = build(rows, f"{int(time.time())}")
    with httpx.Client(base_url=API, headers=token("cli"), timeout=120) as api:
        started = time.perf_counter()
        job = (
            api.post(
                "/v1/ingestion/batches",
                data={"dataset": "transactions"},
                files={"file": ("bench.csv", data)},
            )
            .raise_for_status()
            .json()
        )
        while job["status"] in {"received", "validating", "loading"}:
            time.sleep(0.2)
            job = api.get(f"/v1/ingestion/batches/{job['id']}").raise_for_status().json()
        elapsed = time.perf_counter() - started
    print(
        f"{job['status']}: {job['records_loaded']} loaded, {job['records_quarantined']} quarantined "
        f"of {rows} in {elapsed:.1f}s = {rows / elapsed:.0f} rows/s ({len(data) / 1e6:.1f} MB file)"
    )


if __name__ == "__main__":
    main()
