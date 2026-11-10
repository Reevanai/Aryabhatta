import pytest
from sentinel_core.audit import anchor_chain, record_audit, verify_chain
from sentinel_core.runtime import Platform
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import Tenant, TenantFactory

pytestmark = pytest.mark.integration


async def _append(platform: Platform, tenant: Tenant, n: int) -> None:
    for i in range(n):
        async with platform.db.tenant_tx(tenant.ctx()) as conn:
            await record_audit(
                conn, tenant.ctx(), action="test.action", resource=f"thing:{i}", details={"i": i}
            )


async def test_chain_verifies_and_sequences_are_gap_free(
    platform: Platform, tenant: Tenant
) -> None:
    await _append(platform, tenant, 5)
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        result = await verify_chain(conn, tenant.ctx())
        sequences = [
            r[0]
            for r in await conn.execute(text("SELECT sequence FROM core.audit_event ORDER BY 1"))
        ]
    assert result.verified
    assert result.first_break_sequence is None
    # Sequence 1 is the tenant.create record written at provisioning.
    assert sequences == [1, 2, 3, 4, 5, 6]
    assert result.records_checked == 6


async def test_chains_are_independent_per_tenant(
    platform: Platform, tenant_factory: TenantFactory
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    await _append(platform, tenant_a, 3)
    await _append(platform, tenant_b, 1)
    async with platform.db.tenant_tx(tenant_b.ctx()) as conn:
        assert (await verify_chain(conn, tenant_b.ctx())).records_checked == 2


async def test_concurrent_appends_keep_the_chain_intact(platform: Platform, tenant: Tenant) -> None:
    import asyncio

    await asyncio.gather(*(_append(platform, tenant, 5) for _ in range(8)))
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        result = await verify_chain(conn, tenant.ctx())
    assert result.verified
    assert result.records_checked == 41


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.audit_event SET action = 'x'",
        "DELETE FROM core.audit_event",
        "TRUNCATE core.audit_event",
    ],
)
async def test_application_role_cannot_alter_audit_records(
    platform: Platform, tenant: Tenant, statement: str
) -> None:
    await _append(platform, tenant, 1)
    with pytest.raises(DBAPIError, match="permission denied"):
        async with platform.db.tenant_tx(tenant.ctx()) as conn:
            await conn.execute(text(statement))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE core.audit_event SET action = 'x'",
        "DELETE FROM core.audit_event",
        "TRUNCATE core.audit_event",
    ],
)
async def test_even_the_owner_cannot_alter_audit_records_without_dropping_the_guard(
    platform: Platform, owner_engine: AsyncEngine, tenant: Tenant, statement: str
) -> None:
    from sentinel_core.db import apply_context

    await _append(platform, tenant, 1)
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner_engine.connect() as conn, conn.begin():
            await apply_context(conn, tenant.ctx())
            await conn.execute(text(statement))


async def _tamper(owner: AsyncEngine, tenant: Tenant, statement: str) -> None:
    """Simulate an attacker with owner access who disables the guard triggers."""
    from sentinel_core.db import apply_context

    async with owner.connect() as conn, conn.begin():
        await apply_context(conn, tenant.ctx())
        await conn.execute(text("ALTER TABLE core.audit_event DISABLE TRIGGER USER"))
        await conn.execute(text(statement))
        await conn.execute(text("ALTER TABLE core.audit_event ENABLE TRIGGER USER"))


@pytest.mark.parametrize(
    ("statement", "expected_break"),
    [
        ("UPDATE core.audit_event SET details = '{\"i\": 99}' WHERE sequence = 3", 3),
        ("UPDATE core.audit_event SET actor_id = 'someone-else' WHERE sequence = 4", 4),
        ("DELETE FROM core.audit_event WHERE sequence = 3", 3),
        ("DELETE FROM core.audit_event WHERE sequence = 6", 6),
    ],
)
async def test_tampering_is_detected_and_located(
    platform: Platform,
    owner_engine: AsyncEngine,
    tenant: Tenant,
    statement: str,
    expected_break: int,
) -> None:
    await _append(platform, tenant, 5)
    await _tamper(owner_engine, tenant, statement)
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        result = await verify_chain(conn, tenant.ctx())
    assert not result.verified
    assert result.first_break_sequence == expected_break


async def test_anchor_detects_a_chain_rewritten_end_to_end(
    platform: Platform, owner_engine: AsyncEngine, tenant: Tenant
) -> None:
    """An attacker who rewrites every record and the head consistently is caught by the anchor."""
    from sentinel_core.audit import GENESIS_HASH, canonical_hash

    await _append(platform, tenant, 3)
    key = await platform.keys.get(tenant.ctx(), "audit")
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        assert await anchor_chain(conn, tenant.ctx(), key.key) == 4

    async with owner_engine.connect() as conn, conn.begin():
        from sentinel_core.db import apply_context

        await apply_context(conn, tenant.ctx())
        await conn.execute(text("ALTER TABLE core.audit_event DISABLE TRIGGER USER"))
        rows = (await conn.execute(text("SELECT * FROM core.audit_event ORDER BY sequence"))).all()
        previous = GENESIS_HASH
        for row in rows:
            new_hash = canonical_hash(
                tenant_id=tenant.id,
                sequence=row.sequence,
                event_id=row.id,
                occurred_at=row.occurred_at,
                actor_id="rewritten",
                actor_type=row.actor_type,
                action=row.action,
                resource=row.resource,
                purpose=row.purpose,
                details=row.details,
                previous_hash=previous,
            )
            await conn.execute(
                text(
                    "UPDATE core.audit_event SET actor_id = 'rewritten', previous_hash = :p, "
                    "hash = :h WHERE sequence = :s"
                ),
                {"p": previous, "h": new_hash, "s": row.sequence},
            )
            previous = new_hash
        await conn.execute(text("UPDATE core.audit_chain_head SET hash = :h"), {"h": previous})
        await conn.execute(text("ALTER TABLE core.audit_event ENABLE TRIGGER USER"))

    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        result = await verify_chain(conn, tenant.ctx(), key.key)
    assert not result.verified
    assert result.first_break_sequence == 4
    assert result.last_anchor_at is not None


async def test_anchor_with_a_forged_signature_is_rejected(
    platform: Platform, owner_engine: AsyncEngine, tenant: Tenant
) -> None:
    from sentinel_core.db import apply_context

    await _append(platform, tenant, 2)
    key = await platform.keys.get(tenant.ctx(), "audit")
    async with owner_engine.connect() as conn, conn.begin():
        await apply_context(conn, tenant.ctx())
        await conn.execute(
            text(
                "INSERT INTO core.audit_anchor (tenant_id, sequence, hash, signature) "
                "SELECT tenant_id, sequence, hash, 'forged' FROM core.audit_chain_head"
            )
        )
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        assert not (await verify_chain(conn, tenant.ctx(), key.key)).verified
