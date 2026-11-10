"""Worker loops, the admin CLI and the HTTP key fetcher."""

import asyncio
import base64
import secrets
from typing import Any
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sentinel_core import cli
from sentinel_core.auth import HttpJwksProvider
from sentinel_core.config import Settings
from sentinel_core.errors import Unauthenticated
from sentinel_core.outbox import OutboxRelay
from sentinel_core.runtime import Platform, build_key_service, build_platform
from sentinel_ingest.main import run_batches
from sentinel_ingestion.public import BatchService, IngestionService
from sqlalchemy import text

from tests.conftest import Tenant
from tests.synthetic import PARTY_HEADER, account_row, party_row, to_csv, txn

pytestmark = pytest.mark.integration


async def test_batch_worker_loop_processes_queued_batches_and_stops(
    platform: Platform, tenant: Tenant
) -> None:
    batches = BatchService(platform, IngestionService(platform))
    job, _ = await batches.create(tenant.ctx(), "parties", to_csv(PARTY_HEADER, [party_row(1)]))
    stop = asyncio.Event()
    worker = asyncio.create_task(run_batches(batches, stop, idle_seconds=0.05))
    async with asyncio.timeout(10):
        while (await batches.get(tenant.ctx(), job.id)).status != "completed":
            await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(worker, timeout=5)


class _Recorder:
    def __init__(self, fail_first: bool = False) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self._fail = fail_first

    async def publish(
        self, topic: str, key: bytes, value: bytes, headers: list[tuple[str, bytes]]
    ) -> None:
        if self._fail:
            self._fail = False
            raise ConnectionError("broker unavailable")
        self.sent.append((topic, key))


async def test_relay_loop_survives_a_broker_failure_and_publishes_nothing_twice(
    platform: Platform, tenant: Tenant
) -> None:
    service = IngestionService(platform)
    ctx = tenant.ctx()
    await service.ingest_rows(ctx, "parties", [(1, party_row(1))], source="batch")
    await service.ingest_rows(ctx, "accounts", [(1, account_row(1))], source="batch")
    await service.ingest_rows(
        ctx, "transactions", [(None, txn(i)) for i in range(1, 4)], source="http"
    )

    async def pending() -> int:
        async with platform.db.tenant_tx(ctx) as conn:
            count: int = (
                await conn.execute(
                    text("SELECT count(*) FROM core.outbox WHERE published_at IS NULL")
                )
            ).scalar_one()
            return count

    recorder = _Recorder(fail_first=True)
    stop = asyncio.Event()
    relay = asyncio.create_task(
        OutboxRelay(platform.db, recorder).run_forever(stop, idle_seconds=0.05)
    )
    async with asyncio.timeout(10):
        while await pending():
            await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(relay, timeout=5)
    mine = [key for _, key in recorder.sent if key.startswith(str(tenant.id).encode())]
    assert len(mine) == 3


def _jwks(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": kid, "use": "sig", "alg": "RS256"}]}


async def test_http_jwks_provider_caches_and_refetches_on_key_rotation() -> None:
    keys = {"current": _jwks(rsa.generate_private_key(65537, 2048), "k1")}
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=keys["current"])

    provider = HttpJwksProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await provider.signing_key("https://idp.test/certs", "k1")
    await provider.signing_key("https://idp.test/certs", "k1")
    assert calls == 1
    keys["current"] = _jwks(rsa.generate_private_key(65537, 2048), "k2")
    await provider.signing_key("https://idp.test/certs", "k2")
    assert calls == 2
    with pytest.raises(Unauthenticated):
        await provider.signing_key("https://idp.test/certs", "missing")


async def test_admin_cli_creates_tenant_and_branch_and_anchors(
    settings: Settings,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    suffix = secrets.token_hex(4)
    issuer = f"https://idp.test/realms/cli-{suffix}"

    def run(*argv: str) -> str:
        cli.main(list(argv))
        return capsys.readouterr().out.strip()

    await asyncio.to_thread(run, "migrate")  # already at head: a no-op
    create = (
        "create-tenant",
        "--name",
        f"CLI Bank {suffix}",
        "--issuer",
        issuer,
        "--jwks-uri",
        issuer + "/certs",
    )
    tenant_id = UUID(await asyncio.to_thread(run, *create))
    for _ in range(2):  # idempotent
        await asyncio.to_thread(
            run, "create-branch", "--tenant", str(tenant_id), "--code", "B1", "--name", "One"
        )
    assert await asyncio.to_thread(run, "audit-anchor", "--tenant", str(tenant_id)) == "2"

    tenant = Tenant(tenant_id, issuer)
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        actions = [
            r.action
            for r in await conn.execute(
                text("SELECT action FROM core.audit_event ORDER BY sequence")
            )
        ]
        branches = (await conn.execute(text("SELECT count(*) FROM core.branch"))).scalar_one()
    assert actions == ["tenant.create", "branch.create"]
    assert branches == 1


def test_key_service_configuration_is_checked(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="OPENBAO"):
        build_key_service(settings.model_copy(update={"key_service": "openbao"}))
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        build_key_service(settings.model_copy(update={"local_master_key": None}))
    assert settings.local_master_key is not None
    assert len(base64.b64decode(settings.local_master_key.get_secret_value())) == 32


async def test_platform_builds_from_settings(settings: Settings) -> None:
    built = build_platform(settings)
    try:
        async with built.db.system_tx() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM core.purpose"))).scalar_one() == 5
    finally:
        await built.aclose()
