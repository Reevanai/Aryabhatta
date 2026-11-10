"""Loans, devices, and loading a whole generated data set."""

import csv
import io
from typing import Any

import pytest
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import DATASETS, BatchService, IngestionService
from sentinel_synth.public import LOAD_ORDER, generate, profile, rows
from sqlalchemy import text

from tests.conftest import Tenant, TenantFactory
from tests.synthetic import account_row, device_row, loan_row, party_row

pytestmark = pytest.mark.integration


@pytest.fixture
def service(platform: Platform) -> IngestionService:
    return IngestionService(platform)


async def _seed(service: IngestionService, tenant: Tenant, n: int = 3) -> None:
    ctx = tenant.ctx()
    await service.ingest_rows(
        ctx, "parties", [(i, party_row(i)) for i in range(1, n + 1)], source="batch"
    )
    await service.ingest_rows(
        ctx, "accounts", [(i, account_row(i)) for i in range(1, n + 1)], source="batch"
    )


async def _errors(platform: Platform, tenant: Tenant) -> list[tuple[str, str]]:
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        rows_ = await conn.execute(
            text("SELECT errors FROM ingestion.quarantine_record ORDER BY row_number")
        )
        return [(e["field"], e["message"]) for r in rows_ for e in r.errors]


async def test_loans_load_update_and_check_references(
    platform: Platform, service: IngestionService, tenant: Tenant
) -> None:
    await _seed(service, tenant)
    ctx = tenant.ctx()
    counts = await service.ingest_rows(
        ctx,
        "loans",
        [
            (2, loan_row(1)),
            (3, loan_row(2, customer_id="CUST00009")),
            (4, loan_row(3, account_number="404")),
            (
                5,
                loan_row(
                    4, loan_id="LN-X", customer_id="CUST00001", account_number="00112233000002"
                ),
            ),
        ],
        source="batch",
    )
    assert (counts.loaded, counts.quarantined) == (1, 3)
    assert await _errors(platform, tenant) == [
        ("customer_id", "unknown customer"),
        ("account_number", "unknown account"),
        ("account_number", "account belongs to another customer"),
    ]
    await service.ingest_rows(
        ctx, "loans", [(2, loan_row(1, status="overdue", days_past_due="45"))], source="batch"
    )
    async with platform.db.tenant_tx(ctx) as conn:
        loans = (
            await conn.execute(
                text("SELECT loan_ref, status, days_past_due, principal FROM ingestion.loan")
            )
        ).all()
    assert [(x.loan_ref, x.status, x.days_past_due, str(x.principal)) for x in loans] == [
        ("LN000001", "overdue", 45, "150000.00")
    ]


async def test_devices_link_customers_and_keep_the_earliest_sighting(
    platform: Platform, service: IngestionService, tenant: Tenant
) -> None:
    await _seed(service, tenant)
    ctx = tenant.ctx()
    counts = await service.ingest_rows(
        ctx,
        "devices",
        [
            (2, device_row(1)),
            (3, device_row(1, customer=2)),
            (4, device_row(2, customer=99)),
            (5, device_row(1, first_seen="2025-01-01T00:00:00+00:00", rooted="true")),
        ],
        source="batch",
    )
    assert (counts.loaded, counts.quarantined) == (3, 1)
    async with platform.db.tenant_tx(ctx) as conn:
        links = (
            await conn.execute(
                text(
                    "SELECT device_token, count(*) AS customers, bool_or(rooted) AS rooted, "
                    "min(first_seen) AS first FROM ingestion.device_link GROUP BY device_token"
                )
            )
        ).all()
        dump = (
            await conn.execute(text("SELECT string_agg(d::text, ' ') FROM ingestion.device_link d"))
        ).scalar_one()
    assert len(links) == 1
    assert links[0].device_token.startswith("tok_dev_")
    assert (links[0].customers, links[0].rooted) == (2, True)
    assert links[0].first.isoformat() == "2025-01-01T00:00:00+00:00"
    assert "device-1" not in dump


def _csv(data: list[dict[str, Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(data[0]))
    writer.writeheader()
    writer.writerows(data)
    return out.getvalue().encode()


@pytest.mark.parametrize("institution", ["urban_cooperative_bank", "nbfc"])
async def test_a_generated_data_set_loads_with_nothing_quarantined(
    platform: Platform, service: IngestionService, tenant_factory: TenantFactory, institution: str
) -> None:
    world = generate(profile("tiny", customers=150, days=20, institution=institution))
    data = rows(world)
    tenant = await tenant_factory(tuple(b["code"] for b in data["branches"]))
    batches = BatchService(platform, service)
    assert tuple(LOAD_ORDER) == tuple(DATASETS)
    for dataset in LOAD_ORDER:
        job, _ = await batches.create(tenant.ctx(), dataset, _csv(data[dataset]))
        done = await batches.process(tenant.id, job.id)
        assert (done.status, done.records_quarantined, done.error) == ("completed", 0, None), (
            dataset
        )
        assert done.records_loaded == len(data[dataset])
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        counts = {
            table: (
                await conn.execute(text(f"SELECT count(*) FROM ingestion.{table}"))
            ).scalar_one()
            for table in ("party", "account", "loan", "device_link", "transaction")
        }
        events = (await conn.execute(text("SELECT count(*) FROM core.outbox"))).scalar_one()
        attributes = (
            await conn.execute(
                text("SELECT count(*) FROM ingestion.transaction WHERE attributes ? 'utr'")
            )
        ).scalar_one()
    assert counts == {
        "party": len(world.parties),
        "account": len(world.accounts),
        "loan": len(world.loans),
        "device_link": len(world.devices),
        "transaction": len(world.txns),
    }
    assert events == len(world.txns)
    assert attributes == sum(1 for t in world.txns if "utr" in t.attrs)
