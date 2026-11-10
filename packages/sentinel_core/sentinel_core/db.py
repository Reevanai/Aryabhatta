"""Database access. The only place that opens connections.

Every transaction sets the tenant context with transaction-local settings, which the
row-level security policies read. A transaction without a context sees no tenant rows.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from sentinel_core.context import TenantContext

_SET_CONTEXT = text(
    "SELECT set_config('app.tenant_id', :tenant, true), set_config('app.branch_ids', :branches, true)"
)


async def apply_context(conn: AsyncConnection, ctx: TenantContext) -> None:
    await conn.execute(
        _SET_CONTEXT,
        {"tenant": str(ctx.tenant_id), "branches": ",".join(str(b) for b in ctx.branch_scope)},
    )


class Database:
    """Holds one pool per database role."""

    def __init__(self, app_url: str, vault_url: str, relay_url: str | None = None) -> None:
        self._app = create_async_engine(app_url, pool_pre_ping=True)
        self._vault = create_async_engine(vault_url, pool_pre_ping=True)
        self._relay = create_async_engine(relay_url, pool_pre_ping=True) if relay_url else None

    @asynccontextmanager
    async def tenant_tx(self, ctx: TenantContext) -> AsyncIterator[AsyncConnection]:
        """Transaction as the application role, scoped to a tenant."""
        async with self._app.connect() as conn, conn.begin():
            await apply_context(conn, ctx)
            yield conn

    @asynccontextmanager
    async def vault_tx(self, ctx: TenantContext) -> AsyncIterator[AsyncConnection]:
        """Transaction as the vault role, scoped to a tenant. Used only by tokenisation."""
        async with self._vault.connect() as conn, conn.begin():
            await apply_context(conn, ctx)
            yield conn

    @asynccontextmanager
    async def system_tx(self) -> AsyncIterator[AsyncConnection]:
        """Transaction as the application role with no tenant: only id-only lookup tables."""
        async with self._app.connect() as conn, conn.begin():
            yield conn

    @asynccontextmanager
    async def relay_tx(self) -> AsyncIterator[AsyncConnection]:
        """Transaction as the relay role: the outbox only."""
        if self._relay is None:
            raise RuntimeError("relay database URL is not configured")
        async with self._relay.connect() as conn, conn.begin():
            yield conn

    async def dispose(self) -> None:
        engines: list[AsyncEngine] = [self._app, self._vault]
        if self._relay is not None:
            engines.append(self._relay)
        for engine in engines:
            await engine.dispose()
