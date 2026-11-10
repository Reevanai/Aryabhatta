"""Per-tenant data keys: stored wrapped, unwrapped on demand, cached briefly."""

import time
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sentinel_core.context import TenantContext
from sentinel_core.crypto import KeyService, WrappedKey, new_data_key
from sentinel_core.db import Database

KeyPurpose = Literal["pii", "hmac", "file", "audit"]
KEY_PURPOSES: tuple[KeyPurpose, ...] = ("pii", "hmac", "file", "audit")


@dataclass(frozen=True)
class DataKey:
    key: bytes
    version: int


class TenantKeys:
    def __init__(self, db: Database, key_service: KeyService, ttl_seconds: float = 300.0) -> None:
        self._db = db
        self._keys = key_service
        self._ttl = ttl_seconds
        self._cache: dict[tuple[UUID, str, int | None], tuple[float, DataKey]] = {}

    async def get(
        self, ctx: TenantContext, purpose: KeyPurpose, version: int | None = None
    ) -> DataKey:
        """The data key for a purpose: the latest version, or a specific one for decryption."""
        cache_key = (ctx.tenant_id, purpose, version)
        hit = self._cache.get(cache_key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        async with self._db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT version, wrapped_key, kek_ref FROM core.tenant_key "
                        "WHERE purpose = :p AND (CAST(:v AS int) IS NULL OR version = :v) "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"p": purpose, "v": version},
                )
            ).first()
        if row is None:
            raise KeyError(f"no {purpose} key for tenant {ctx.tenant_id}")
        key = await self._keys.unwrap(
            ctx.tenant_id, WrappedKey(bytes(row.wrapped_key), row.kek_ref)
        )
        data_key = DataKey(key, row.version)
        self._cache[cache_key] = (time.monotonic() + self._ttl, data_key)
        return data_key

    def forget(self, tenant_id: UUID) -> None:
        for cache_key in [k for k in self._cache if k[0] == tenant_id]:
            del self._cache[cache_key]


async def create_tenant_keys(
    conn: AsyncConnection, key_service: KeyService, tenant_id: UUID, version: int = 1
) -> None:
    """Generate and store one wrapped data key per purpose. Caller sets the tenant context."""
    await key_service.ensure_tenant_key(tenant_id)
    for purpose in KEY_PURPOSES:
        wrapped = await key_service.wrap(tenant_id, new_data_key())
        await conn.execute(
            text(
                "INSERT INTO core.tenant_key (tenant_id, purpose, version, wrapped_key, kek_ref) "
                "VALUES (:t, :p, :v, :w, :r)"
            ),
            {
                "t": tenant_id,
                "p": purpose,
                "v": version,
                "w": wrapped.ciphertext,
                "r": wrapped.kek_ref,
            },
        )
