"""Tests against the real broker and the real OpenBao."""

import asyncio
import json
import secrets
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sentinel_core.crypto import OpenBaoKeyService, WrappedKey, new_data_key
from sentinel_core.events import validate_event
from sentinel_core.outbox import KafkaPublisher, OutboxRelay
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import TRANSACTION_TOPIC, IngestionService
from sqlalchemy import text

from tests.conftest import KAFKA_BOOTSTRAP, OPENBAO_ADDR, OPENBAO_TOKEN, Tenant
from tests.synthetic import account_row, party_row, txn

pytestmark = pytest.mark.integration


@pytest.mark.kafka
async def test_outbox_relay_publishes_to_kafka_in_order_and_once(
    platform: Platform, tenant: Tenant
) -> None:
    service = IngestionService(platform)
    ctx = tenant.ctx()
    await service.ingest_rows(ctx, "parties", [(1, party_row(1))], source="batch")
    await service.ingest_rows(ctx, "accounts", [(1, account_row(1))], source="batch")
    await service.ingest_rows(
        ctx, "transactions", [(None, txn(i)) for i in range(1, 6)], source="http"
    )

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP, acks="all", enable_idempotence=True
    )
    consumer = AIOKafkaConsumer(
        TRANSACTION_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=f"test-{secrets.token_hex(4)}",
        auto_offset_reset="earliest",
    )
    await producer.start()
    await consumer.start()
    try:
        relay = OutboxRelay(platform.db, KafkaPublisher(producer.send_and_wait), batch_size=1000)
        while await relay.run_once():
            pass
        assert await relay.run_once() == 0

        mine: list[dict[str, object]] = []
        deadline = asyncio.get_running_loop().time() + 20
        while len(mine) < 5 and asyncio.get_running_loop().time() < deadline:
            polled = await consumer.getmany(timeout_ms=500)
            for records in polled.values():
                for record in records:
                    if dict(record.headers).get("tenant_id") == str(tenant.id).encode():
                        assert (
                            record.key
                            == f"{tenant.id}:{json.loads(record.value)['payload']['account_token']}".encode()
                        )
                        mine.append(json.loads(record.value))
    finally:
        await consumer.stop()
        await producer.stop()

    assert [e["payload"]["source_reference"] for e in mine] == [f"TXN-{i:08d}" for i in range(1, 6)]  # type: ignore[index]
    for event in mine:
        validate_event(event)
        assert "00112233" not in json.dumps(event)
    async with platform.db.tenant_tx(ctx) as conn:
        pending = (
            await conn.execute(text("SELECT count(*) FROM core.outbox WHERE published_at IS NULL"))
        ).scalar_one()
    assert pending == 0


@pytest.mark.openbao
async def test_openbao_transit_wraps_and_unwraps_per_tenant() -> None:
    import httpx

    service = OpenBaoKeyService(OPENBAO_ADDR, OPENBAO_TOKEN)
    tenant_a, tenant_b = uuid4(), uuid4()
    try:
        await service.ensure_tenant_key(tenant_a)
        await service.ensure_tenant_key(tenant_a)  # idempotent
        await service.ensure_tenant_key(tenant_b)
        data_key = new_data_key()
        wrapped = await service.wrap(tenant_a, data_key)
        assert wrapped.ciphertext.startswith(b"vault:v1:")
        assert data_key not in wrapped.ciphertext
        assert await service.unwrap(tenant_a, wrapped) == data_key
        with pytest.raises(httpx.HTTPStatusError):
            await service.unwrap(tenant_b, WrappedKey(wrapped.ciphertext, wrapped.kek_ref))

        await service.destroy_tenant_key(tenant_a)
        with pytest.raises(httpx.HTTPStatusError):
            await service.unwrap(tenant_a, wrapped)
        await service.destroy_tenant_key(tenant_b)
    finally:
        await service.aclose()
