"""Shared fixtures.

Integration tests need the development dependencies (`make deps-up`): PostgreSQL with the
Sentinel roles, a Kafka-API broker and OpenBao. Each test session creates and drops its own
database. All data is synthetic.
"""

import base64
import os
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sentinel_api.app import create_app
from sentinel_core.cli import migrate
from sentinel_core.config import Settings
from sentinel_core.context import TenantContext
from sentinel_core.crypto import LocalKeyService
from sentinel_core.db import apply_context
from sentinel_core.ids import uuid7
from sentinel_core.provisioning import NewTenant, create_tenant
from sentinel_core.runtime import Platform, build_platform
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PG_HOST = os.environ.get("SENTINEL_TEST_PG_HOST", "127.0.0.1:55432")
PG_ADMIN_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "dev-postgres")
PASSWORDS = {
    "sentinel_owner": os.environ.get("SENTINEL_DB_OWNER_PASSWORD", "dev-owner"),
    "sentinel_app": os.environ.get("SENTINEL_DB_APP_PASSWORD", "dev-app"),
    "sentinel_vault": os.environ.get("SENTINEL_DB_VAULT_PASSWORD", "dev-vault"),
    "sentinel_relay": os.environ.get("SENTINEL_DB_RELAY_PASSWORD", "dev-relay"),
}
KAFKA_BOOTSTRAP = os.environ.get("SENTINEL_TEST_KAFKA", "127.0.0.1:19092")
OPENBAO_ADDR = os.environ.get("SENTINEL_TEST_OPENBAO_ADDR", "http://127.0.0.1:8200")
OPENBAO_TOKEN = os.environ.get("OPENBAO_DEV_ROOT_TOKEN", "dev-root-token")
AUDIENCE = "sentinel-api"


def _url(role: str, database: str) -> str:
    return f"postgresql+asyncpg://{role}:{PASSWORDS[role]}@{PG_HOST}/{database}"


@pytest.fixture(scope="session")
async def database_name() -> AsyncIterator[str]:
    name = f"sentinel_test_{secrets.token_hex(4)}"
    admin = create_async_engine(
        f"postgresql+asyncpg://postgres:{PG_ADMIN_PASSWORD}@{PG_HOST}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}" OWNER sentinel_owner'))
    yield name
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture(scope="session")
async def owner_engine(database_name: str) -> AsyncIterator[AsyncEngine]:
    import asyncio

    await asyncio.to_thread(migrate, _url("sentinel_owner", database_name))
    engine = create_async_engine(_url("sentinel_owner", database_name))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def settings(database_name: str, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    files: Path = tmp_path_factory.mktemp("files")
    return Settings(
        database_url_app=_url("sentinel_app", database_name),
        database_url_vault=_url("sentinel_vault", database_name),
        database_url_relay=_url("sentinel_relay", database_name),
        database_url_owner=_url("sentinel_owner", database_name),
        local_master_key=base64.b64encode(secrets.token_bytes(32)).decode(),  # type: ignore[arg-type]
        kafka_bootstrap=KAFKA_BOOTSTRAP,
        filestore_dir=files,
        oidc_audience=AUDIENCE,
    )


@pytest.fixture(scope="session")
def key_service(settings: Settings) -> LocalKeyService:
    assert settings.local_master_key is not None
    return LocalKeyService(base64.b64decode(settings.local_master_key.get_secret_value()))


@pytest.fixture(scope="session")
async def platform(
    settings: Settings, key_service: LocalKeyService, owner_engine: AsyncEngine
) -> AsyncIterator[Platform]:
    built = build_platform(settings, key_service)
    yield built
    await built.aclose()


@dataclass
class Tenant:
    id: UUID
    issuer: str
    branches: dict[str, UUID] = field(default_factory=dict)

    def ctx(self, purpose: str = "service_operations", *branches: UUID) -> TenantContext:
        return TenantContext(self.id, "test-actor", "service", purpose, tuple(branches))


TenantFactory = Callable[..., Awaitable[Tenant]]


@pytest.fixture(scope="session")
def tenant_factory(owner_engine: AsyncEngine, key_service: LocalKeyService) -> TenantFactory:
    async def make(branch_codes: tuple[str, ...] = ("BR001", "BR002")) -> Tenant:
        suffix = secrets.token_hex(4)
        issuer = f"https://idp.test/realms/t-{suffix}"
        tenant_id = await create_tenant(
            owner_engine,
            key_service,
            NewTenant(
                f"Synthetic Bank {suffix}", "urban_cooperative_bank", issuer, issuer + "/certs"
            ),
        )
        tenant = Tenant(tenant_id, issuer)
        async with owner_engine.connect() as conn, conn.begin():
            await apply_context(conn, tenant.ctx())
            for code in branch_codes:
                branch_id = uuid7()
                await conn.execute(
                    text(
                        "INSERT INTO core.branch (id, tenant_id, code, name) VALUES (:i, :t, :c, :n)"
                    ),
                    {"i": branch_id, "t": tenant_id, "c": code, "n": f"Branch {code}"},
                )
                tenant.branches[code] = branch_id
        return tenant

    return make


@pytest.fixture
async def tenant(tenant_factory: TenantFactory) -> Tenant:
    return await tenant_factory()


class Signer:
    """A stand-in identity provider: signs tokens and serves the matching key."""

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key"

    async def signing_key(self, jwks_uri: str, kid: str) -> Any:
        if kid != self.kid:
            from sentinel_core.errors import Unauthenticated

            raise Unauthenticated("unknown signing key")
        return self._key.public_key()

    def token(
        self,
        tenant: Tenant,
        roles: tuple[str, ...] = ("tenant_admin",),
        *,
        branch_ids: tuple[UUID, ...] = (),
        subject: str = "user-1",
        service: bool = False,
        **overrides: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": tenant.issuer,
            "sub": subject,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 300,
            "tenant_id": str(tenant.id),
            "branch_ids": [str(b) for b in branch_ids],
            "realm_access": {"roles": list(roles)},
        }
        if service:
            claims["client_id"] = subject
        claims.update(overrides)
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.kid})

    def headers(
        self, tenant: Tenant, roles: tuple[str, ...] = ("tenant_admin",), **kw: Any
    ) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(tenant, roles, **kw)}"}


@pytest.fixture(scope="session")
def signer() -> Signer:
    return Signer()


@pytest.fixture(scope="session")
async def client(platform: Platform, signer: Signer) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(platform, jwks=signer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
