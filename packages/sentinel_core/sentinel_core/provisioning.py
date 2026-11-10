"""Tenant provisioning. Runs as the owner role; the application role cannot create tenants."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sentinel_core.audit import GENESIS_HASH, record_audit
from sentinel_core.context import TenantContext
from sentinel_core.crypto import KeyService
from sentinel_core.db import apply_context
from sentinel_core.ids import uuid7
from sentinel_core.tenant_keys import create_tenant_keys


@dataclass(frozen=True)
class NewTenant:
    name: str
    institution_type: str
    oidc_issuer: str
    jwks_uri: str
    tenant_id: UUID | None = None


async def create_tenant(owner: AsyncEngine, key_service: KeyService, spec: NewTenant) -> UUID:
    """Create a tenant with its issuer mapping, audit chain and data keys. Idempotent on id."""
    tenant_id = spec.tenant_id or uuid7()
    ctx = TenantContext(tenant_id, "provisioning")
    async with owner.connect() as conn, conn.begin():
        await apply_context(conn, ctx)
        exists = (await conn.execute(text("SELECT 1 FROM core.tenant"))).first()
        if exists:
            return tenant_id
        await conn.execute(
            text("INSERT INTO core.tenant (id, name, institution_type) VALUES (:id, :n, :t)"),
            {"id": tenant_id, "n": spec.name, "t": spec.institution_type},
        )
        await conn.execute(
            text(
                "INSERT INTO core.tenant_issuer (issuer, tenant_id, jwks_uri) VALUES (:i, :t, :j)"
            ),
            {"i": spec.oidc_issuer, "t": tenant_id, "j": spec.jwks_uri},
        )
        await conn.execute(
            text(
                "INSERT INTO core.audit_chain_head (tenant_id, sequence, hash) VALUES (:t, 0, :h)"
            ),
            {"t": tenant_id, "h": GENESIS_HASH},
        )
        await create_tenant_keys(conn, key_service, tenant_id)
        await record_audit(
            conn,
            ctx,
            action="tenant.create",
            resource=f"tenant:{tenant_id}",
            details={"institution_type": spec.institution_type},
        )
    return tenant_id
