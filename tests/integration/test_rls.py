"""Tenant isolation is enforced by the database for every table."""

import pytest
from sentinel_core.context import TenantContext
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import IngestionService
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import Tenant, TenantFactory
from tests.synthetic import account_row, party_row, txn

pytestmark = pytest.mark.integration

# Tables that deliberately have no row-level security, with the reason.
EXEMPT = {
    "core.purpose": "global reference data",
    "core.data_catalogue": "global reference data",
    "core.field_purpose": "global reference data",
    "core.tenant_issuer": "issuer-to-tenant lookup, read before a tenant is known",
    "ingestion.batch_queue": "id-only work queue, read before a tenant is known",
    "public.alembic_version": "migration bookkeeping",
}


async def _tables(owner: AsyncEngine) -> list[tuple[str, bool, bool, bool]]:
    async with owner.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT n.nspname || '.' || c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "c.relispartition FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('core', 'vault', 'ingestion', 'public')"
            )
        )
        return [(r[0], r[1], r[2], r[3]) for r in rows]


async def test_every_table_has_forced_row_level_security_or_a_documented_exemption(
    owner_engine: AsyncEngine,
) -> None:
    missing = [
        name
        for name, enabled, forced, is_partition in await _tables(owner_engine)
        if not is_partition and name not in EXEMPT and not (enabled and forced)
    ]
    assert missing == []


async def test_application_roles_cannot_bypass_row_level_security(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('sentinel_app', 'sentinel_vault', 'sentinel_relay', 'sentinel_owner')"
            )
        )
        assert {r.rolname: (r.rolsuper, r.rolbypassrls) for r in rows} == {
            "sentinel_app": (False, False),
            "sentinel_vault": (False, False),
            "sentinel_relay": (False, False),
            "sentinel_owner": (False, False),
        }


async def test_application_role_has_no_direct_access_to_partitions(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as conn:
        granted = (
            await conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relispartition AND n.nspname = 'ingestion' "
                    "AND has_table_privilege('sentinel_app', c.oid, 'SELECT')"
                )
            )
        ).all()
    assert granted == []


@pytest.fixture
async def two_tenants(platform: Platform, tenant_factory: TenantFactory) -> tuple[Tenant, Tenant]:
    tenants = (await tenant_factory(), await tenant_factory())
    service = IngestionService(platform)
    for tenant in tenants:
        ctx = tenant.ctx()
        await service.ingest_rows(ctx, "parties", [(1, party_row(1))], source="batch")
        await service.ingest_rows(ctx, "accounts", [(1, account_row(1))], source="batch")
        await service.ingest_rows(ctx, "transactions", [(None, txn(1))], source="http")
        await service.ingest_rows(ctx, "parties", [(2, party_row(2, pan="bad"))], source="batch")
    return tenants


async def test_a_tenant_sees_only_its_own_rows_in_every_table(
    platform: Platform, owner_engine: AsyncEngine, two_tenants: tuple[Tenant, Tenant]
) -> None:
    tenant_a, tenant_b = two_tenants
    checked = 0
    for name, _, _, is_partition in await _tables(owner_engine):
        if is_partition or name in EXEMPT:
            continue
        opener = platform.db.vault_tx if name.startswith("vault.") else platform.db.tenant_tx
        async with opener(tenant_a.ctx()) as conn:
            rows = (await conn.execute(text(f"SELECT tenant_id FROM {name}"))).all()  # noqa: S608
        assert rows, f"{name} has no rows for the tenant; the isolation check would prove nothing"
        assert {r.tenant_id for r in rows} == {tenant_a.id}, name
        assert tenant_b.id not in {r.tenant_id for r in rows}
        checked += 1
    assert checked >= 12


async def test_a_session_without_a_tenant_sees_nothing(
    platform: Platform, two_tenants: tuple[Tenant, Tenant]
) -> None:
    async with platform.db.system_tx() as conn:
        for table in (
            "core.tenant",
            "ingestion.party",
            "ingestion.transaction",
            "core.audit_event",
        ):
            count = (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608
            assert count == 0, table


async def test_a_tenant_cannot_write_rows_for_another_tenant(
    platform: Platform, two_tenants: tuple[Tenant, Tenant]
) -> None:
    tenant_a, tenant_b = two_tenants
    with pytest.raises(DBAPIError, match="row-level security"):
        async with platform.db.tenant_tx(tenant_a.ctx()) as conn:
            await conn.execute(
                text(
                    "INSERT INTO core.branch (id, tenant_id, code, name) "
                    "VALUES (gen_random_uuid(), :t, 'X', 'x')"
                ),
                {"t": tenant_b.id},
            )


async def test_a_tenant_cannot_update_or_take_over_another_tenants_rows(
    platform: Platform, two_tenants: tuple[Tenant, Tenant]
) -> None:
    tenant_a, tenant_b = two_tenants
    async with platform.db.tenant_tx(tenant_a.ctx()) as conn:
        changed = await conn.execute(
            text("UPDATE core.branch SET name = 'taken' WHERE tenant_id = :t"), {"t": tenant_b.id}
        )
        assert changed.rowcount == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        async with platform.db.tenant_tx(tenant_a.ctx()) as conn:
            await conn.execute(text("UPDATE core.branch SET tenant_id = :t"), {"t": tenant_b.id})


async def test_application_role_cannot_reach_the_vault(platform: Platform, tenant: Tenant) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with platform.db.tenant_tx(tenant.ctx()) as conn:
            await conn.execute(text("SELECT * FROM vault.pii_token"))


async def test_vault_role_cannot_reach_business_tables(platform: Platform, tenant: Tenant) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with platform.db.vault_tx(tenant.ctx()) as conn:
            await conn.execute(text("SELECT * FROM ingestion.party"))


async def test_relay_role_sees_only_the_outbox(
    platform: Platform, two_tenants: tuple[Tenant, Tenant]
) -> None:
    async with platform.db.relay_tx() as conn:
        tenants = {
            r.tenant_id
            for r in await conn.execute(text("SELECT DISTINCT tenant_id FROM core.outbox"))
        }
        assert {t.id for t in two_tenants} <= tenants
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with platform.db.relay_tx() as conn:
            await conn.execute(text("SELECT * FROM ingestion.party"))


async def test_application_role_cannot_create_tenants_or_keys(
    platform: Platform, tenant: Tenant
) -> None:
    for statement in (
        "INSERT INTO core.tenant (id, name, institution_type) VALUES (gen_random_uuid(), 'x', 'nbfc')",
        "DELETE FROM core.tenant_key",
        "UPDATE core.tenant SET status = 'suspended'",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with platform.db.tenant_tx(tenant.ctx()) as conn:
                await conn.execute(text(statement))


async def test_branch_scoped_sessions_see_only_their_branches(
    platform: Platform, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory()
    service = IngestionService(platform)
    ctx = tenant.ctx()
    await service.ingest_rows(
        ctx, "parties", [(1, party_row(1)), (2, party_row(2))], source="batch"
    )
    await service.ingest_rows(
        ctx,
        "accounts",
        [(1, account_row(1, "BR001")), (2, account_row(2, "BR002"))],
        source="batch",
    )
    await service.ingest_rows(
        ctx, "transactions", [(None, txn(1, account=1)), (None, txn(2, account=2))], source="http"
    )
    scoped = TenantContext(
        tenant.id, "branch-user", "user", branch_scope=(tenant.branches["BR001"],)
    )
    async with platform.db.tenant_tx(scoped) as conn:
        for table, column in (
            ("ingestion.account", "branch_id"),
            ("ingestion.transaction", "branch_id"),
            ("core.branch", "id"),
        ):
            seen = {r[0] for r in await conn.execute(text(f"SELECT {column} FROM {table}"))}  # noqa: S608
            assert seen == {tenant.branches["BR001"]}, table
    async with platform.db.tenant_tx(ctx) as conn:
        total = (
            await conn.execute(text("SELECT count(*) FROM ingestion.transaction"))
        ).scalar_one()
    assert total == 2
