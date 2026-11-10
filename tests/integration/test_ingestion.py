import json

import pytest
from sentinel_core.events import validate_event
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import BatchService, IngestionService, IntakeMessage, handle_intake
from sqlalchemy import text

from tests.conftest import Tenant, TenantFactory
from tests.synthetic import (
    ACCOUNT_HEADER,
    PARTY_HEADER,
    TXN_HEADER,
    account_row,
    flat_txn,
    party_row,
    to_csv,
    txn,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def service(platform: Platform) -> IngestionService:
    return IngestionService(platform)


@pytest.fixture
def batches(platform: Platform, service: IngestionService) -> BatchService:
    return BatchService(platform, service)


async def _run(batches: BatchService, tenant: Tenant, dataset: str, data: bytes) -> object:
    job, created = await batches.create(tenant.ctx(), dataset, data)
    assert created
    claimed = None
    while (next_claim := await batches.claim_next()) is not None:
        processed = await batches.process(*next_claim)
        if processed.id == job.id:
            claimed = processed
    assert claimed is not None
    return claimed


async def _seed(batches: BatchService, tenant: Tenant, n: int = 3) -> None:
    await _run(
        batches, tenant, "parties", to_csv(PARTY_HEADER, [party_row(i) for i in range(1, n + 1)])
    )
    await _run(
        batches,
        tenant,
        "accounts",
        to_csv(ACCOUNT_HEADER, [account_row(i) for i in range(1, n + 1)]),
    )


async def test_batch_loads_valid_rows_and_quarantines_bad_ones(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    rows = [party_row(1), party_row(2, pan="NOT-A-PAN"), party_row(3, phone="12"), party_row(4)]
    job = await _run(batches, tenant, "parties", to_csv(PARTY_HEADER, rows))
    assert (job.status, job.records_total, job.records_loaded, job.records_quarantined) == (  # type: ignore[attr-defined]
        "completed_with_errors",
        4,
        2,
        2,
    )
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        quarantined = (
            await conn.execute(
                text(
                    "SELECT row_number, errors FROM ingestion.quarantine_record ORDER BY row_number"
                )
            )
        ).all()
        parties = (await conn.execute(text("SELECT count(*) FROM ingestion.party"))).scalar_one()
    assert parties == 2
    # Row numbers are file line numbers: the header is line 1.
    assert [q.row_number for q in quarantined] == [3, 4]
    assert quarantined[0].errors[0]["field"] == "pan"
    assert quarantined[1].errors[0]["field"] == "phone"


async def test_no_clear_personal_data_is_stored_outside_the_vault(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    await _seed(batches, tenant, 2)
    await _run(batches, tenant, "transactions", to_csv(TXN_HEADER, [flat_txn(1), flat_txn(2, 2)]))
    await _run(
        batches, tenant, "parties", to_csv(PARTY_HEADER, [party_row(9, pan="LEAKY-PAN-VALUE")])
    )
    needles = [
        "Test Person 1",
        "ABCPT0001Z",
        "9000000001",
        "person1@example.invalid",
        "Sample Street",
        "00112233000001",
        "998877665544",
        "LEAKY-PAN-VALUE",
        "CUST00001",
    ]
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        for table in (
            "ingestion.party",
            "ingestion.account",
            "ingestion.transaction",
            "ingestion.quarantine_record",
            "ingestion.batch_job",
            "core.outbox",
            "core.audit_event",
        ):
            dump = (
                await conn.execute(
                    text(f"SELECT coalesce(string_agg(t::text, ' '), '') FROM {table} t")
                )
            ).scalar_one()
            for needle in needles:
                assert needle not in dump, f"{needle!r} found in {table}"


async def test_uploaded_file_is_encrypted_at_rest(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    data = to_csv(PARTY_HEADER, [party_row(1)])
    job, _ = await batches.create(tenant.ctx(), "parties", data)
    stored = [p.read_bytes() for p in (platform.settings.filestore_dir / str(tenant.id)).iterdir()]
    assert stored
    assert all(b"Test Person" not in blob and b"ABCPT0001Z" not in blob for blob in stored)
    await batches.process(tenant.id, job.id)


async def test_quarantined_row_can_be_read_back_by_its_tenant_only(
    platform: Platform, service: IngestionService, tenant_factory: TenantFactory
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    await service.ingest_rows(
        tenant_a.ctx(), "parties", [(7, party_row(7, pan="bad"))], source="batch"
    )
    async with platform.db.tenant_tx(tenant_a.ctx()) as conn:
        record_id = (
            await conn.execute(text("SELECT id FROM ingestion.quarantine_record"))
        ).scalar_one()
    assert (await service.read_quarantined(tenant_a.ctx(), record_id))[
        "full_name"
    ] == "Test Person 7"
    with pytest.raises(Exception, match="No row"):
        await service.read_quarantined(tenant_b.ctx(), record_id)


async def test_same_file_is_accepted_once(batches: BatchService, tenant: Tenant) -> None:
    data = to_csv(PARTY_HEADER, [party_row(1)])
    first, created = await batches.create(tenant.ctx(), "parties", data)
    second, created_again = await batches.create(tenant.ctx(), "parties", data)
    assert created
    assert not created_again
    assert first.id == second.id


async def test_reloading_parties_updates_rather_than_duplicates(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    await _run(batches, tenant, "parties", to_csv(PARTY_HEADER, [party_row(1)]))
    await _run(
        batches, tenant, "parties", to_csv(PARTY_HEADER, [party_row(1, kyc_risk_category="high")])
    )
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        rows = (await conn.execute(text("SELECT kyc_risk_category FROM ingestion.party"))).all()
    assert [r.kyc_risk_category for r in rows] == ["high"]


async def test_accounts_with_unknown_references_are_quarantined(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    await _run(batches, tenant, "parties", to_csv(PARTY_HEADER, [party_row(1)]))
    rows = [account_row(1), account_row(2), account_row(1, branch="NOPE", account_number="777")]
    job = await _run(batches, tenant, "accounts", to_csv(ACCOUNT_HEADER, rows))
    assert (job.records_loaded, job.records_quarantined) == (1, 2)  # type: ignore[attr-defined]
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        errors = [
            r.errors[0]
            for r in await conn.execute(
                text("SELECT errors FROM ingestion.quarantine_record ORDER BY row_number")
            )
        ]
    assert [(e["field"], e["message"]) for e in errors] == [
        ("customer_id", "unknown customer"),
        ("branch_code", "unknown branch"),
    ]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"customer_id,full_name\nCUST1,Test\n", "missing:party_type"),
        (PARTY_HEADER.encode() + b",salary\n", "unexpected:salary"),
        (b"\xff\xfe\x00bad", "file is not UTF-8"),
    ],
)
async def test_unusable_files_fail_with_a_reason(
    batches: BatchService, tenant: Tenant, data: bytes, expected: str
) -> None:
    job = await _run(batches, tenant, "parties", data)
    assert job.status == "failed"  # type: ignore[attr-defined]
    assert expected in job.error  # type: ignore[attr-defined]


async def test_rows_with_too_many_cells_are_quarantined(
    batches: BatchService, tenant: Tenant
) -> None:
    data = (
        to_csv(PARTY_HEADER, [party_row(1)])
        + b'"CUST2","individual","X","","","","","","","","extra"\n'
    )
    job = await _run(batches, tenant, "parties", data)
    assert (job.records_loaded, job.records_quarantined) == (1, 1)  # type: ignore[attr-defined]


async def test_transactions_are_stored_partitioned_and_emit_valid_events(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    await _seed(batches, tenant, 2)
    rows = [
        flat_txn(1, booked_at="2026-01-15T10:00:00+05:30"),
        flat_txn(2, 2, booked_at="2026-02-20T10:00:00+05:30", branch_code="BR002"),
        flat_txn(3, 9),
    ]
    job = await _run(batches, tenant, "transactions", to_csv(TXN_HEADER, rows))
    assert (job.records_loaded, job.records_quarantined) == (2, 1)  # type: ignore[attr-defined]
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        stored = (
            await conn.execute(
                text(
                    "SELECT source_reference, amount, branch_id, tableoid::regclass::text AS part "
                    "FROM ingestion.transaction ORDER BY source_reference"
                )
            )
        ).all()
        events = [
            r.event for r in await conn.execute(text("SELECT event FROM core.outbox ORDER BY seq"))
        ]
    assert [(s.source_reference, str(s.amount), s.part) for s in stored] == [
        ("TXN-00000001", "2500.50", "ingestion.transaction_202601"),
        ("TXN-00000002", "2500.50", "ingestion.transaction_202602"),
    ]
    # A transaction may be booked at a branch other than the account's own.
    assert stored[0].branch_id == tenant.branches["BR001"]
    assert stored[1].branch_id == tenant.branches["BR002"]
    assert len(events) == 2
    for event in events:
        validate_event(event)
        assert event["tenant_id"] == str(tenant.id)
        assert event["payload"]["account_token"].startswith("tok_acct_")
        assert event["payload"]["party_token"].startswith("tok_cust_")


async def test_duplicate_transactions_are_ignored_and_emit_no_second_event(
    platform: Platform, service: IngestionService, batches: BatchService, tenant: Tenant
) -> None:
    await _seed(batches, tenant, 1)
    first = await service.ingest_rows(tenant.ctx(), "transactions", [(None, txn(1))], source="http")
    again = await service.ingest_rows(tenant.ctx(), "transactions", [(None, txn(1))], source="http")
    assert (first.loaded, first.duplicates) == (1, 0)
    assert (again.loaded, again.duplicates, again.quarantined) == (0, 1, 0)
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        assert (await conn.execute(text("SELECT count(*) FROM core.outbox"))).scalar_one() == 1


async def test_ingestion_refuses_a_branch_scoped_context(
    service: IngestionService, tenant: Tenant
) -> None:
    scoped = tenant.ctx("service_operations", tenant.branches["BR001"])
    with pytest.raises(PermissionError):
        await service.ingest_rows(scoped, "parties", [(1, party_row(1))], source="batch")


async def test_stream_intake_ingests_per_tenant_and_quarantines_bad_messages(
    platform: Platform,
    service: IngestionService,
    batches: BatchService,
    tenant_factory: TenantFactory,
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    await _seed(batches, tenant_a, 1)
    await _seed(batches, tenant_b, 1)

    def message(tenant: Tenant | None, body: bytes) -> IntakeMessage:
        headers = [("tenant_id", str(tenant.id).encode())] if tenant else []
        return IntakeMessage(body, headers)

    counts = await handle_intake(
        service,
        [
            message(tenant_a, json.dumps(txn(1)).encode()),
            message(tenant_b, json.dumps(txn(1)).encode()),
            message(
                tenant_a, json.dumps(txn(2, amount={"amount": "-1", "currency": "INR"})).encode()
            ),
            message(tenant_a, b"not json"),
            message(None, json.dumps(txn(3)).encode()),
            IntakeMessage(json.dumps(txn(4)).encode(), [("tenant_id", b"not-a-uuid")]),
        ],
    )
    assert (counts.total, counts.loaded, counts.quarantined) == (4, 2, 2)
    for tenant, expected in ((tenant_a, 1), (tenant_b, 1)):
        async with platform.db.tenant_tx(tenant.ctx()) as conn:
            stored = (
                await conn.execute(text("SELECT count(*) FROM ingestion.transaction"))
            ).scalar_one()
            assert stored == expected
    async with platform.db.tenant_tx(tenant_a.ctx()) as conn:
        sources = (
            await conn.execute(
                text("SELECT count(*) FROM ingestion.quarantine_record WHERE source = 'stream'")
            )
        ).scalar_one()
    assert sources == 2


async def test_batch_lifecycle_is_audited(
    platform: Platform, batches: BatchService, tenant: Tenant
) -> None:
    await _run(batches, tenant, "parties", to_csv(PARTY_HEADER, [party_row(1)]))
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        actions = [
            r.action
            for r in await conn.execute(
                text("SELECT action FROM core.audit_event ORDER BY sequence")
            )
        ]
    assert actions == ["tenant.create", "ingestion.batch.create", "ingestion.batch.complete"]


async def test_a_stale_claim_is_retried_then_abandoned(
    platform: Platform, batches: BatchService, tenant: Tenant, owner_engine: object
) -> None:
    job, _ = await batches.create(tenant.ctx(), "parties", to_csv(PARTY_HEADER, [party_row(1)]))
    assert await batches.claim_next() == (tenant.id, job.id)
    assert await batches.claim_next() is None
    async with platform.db.system_tx() as conn:
        await conn.execute(
            text(
                "UPDATE ingestion.batch_queue SET claimed_at = now() - interval '1 hour' WHERE batch_id = :b"
            ),
            {"b": job.id},
        )
    assert await batches.claim_next() == (tenant.id, job.id)
    async with platform.db.system_tx() as conn:
        await conn.execute(
            text(
                "UPDATE ingestion.batch_queue SET claimed_at = now() - interval '1 hour', attempts = 3 "
                "WHERE batch_id = :b"
            ),
            {"b": job.id},
        )
    assert await batches.claim_next() is None
    async with platform.db.system_tx() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM ingestion.batch_queue WHERE batch_id = :b"), {"b": job.id}
            )
        ).scalar_one()
    assert status == "dead"
