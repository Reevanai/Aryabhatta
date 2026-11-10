import time
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import BatchService, IngestionService
from sqlalchemy import text

from tests.conftest import Signer, Tenant, TenantFactory
from tests.synthetic import ACCOUNT_HEADER, PARTY_HEADER, account_row, party_row, to_csv, txn

pytestmark = pytest.mark.integration
PROBLEM = "application/problem+json"


async def _drain(platform: Platform) -> None:
    batches = BatchService(platform, IngestionService(platform))
    while (claimed := await batches.claim_next()) is not None:
        await batches.process(*claimed)


async def _upload(
    client: httpx.AsyncClient, headers: dict[str, str], dataset: str, data: bytes
) -> httpx.Response:
    return await client.post(
        "/v1/ingestion/batches",
        headers=headers,
        data={"dataset": dataset},
        files={"file": ("data.csv", data, "text/csv")},
    )


# Authentication ---------------------------------------------------------------------------


async def test_missing_token_is_401_problem(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/tenant")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM)
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 10},
        {"aud": "some-other-api"},
        {"iss": "https://unknown.test/realms/x"},
        {"tenant_id": str(uuid4())},
    ],
)
async def test_bad_tokens_are_rejected(
    client: httpx.AsyncClient, signer: Signer, tenant: Tenant, overrides: dict[str, Any]
) -> None:
    token = signer.token(tenant, ("tenant_admin",), **overrides)
    response = await client.get("/v1/tenant", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_token_signed_by_another_key_is_rejected(
    client: httpx.AsyncClient, tenant: Tenant
) -> None:
    forged = Signer().token(tenant)
    response = await client.get("/v1/tenant", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_unsigned_token_is_rejected(client: httpx.AsyncClient, tenant: Tenant) -> None:
    import jwt

    token = jwt.encode(
        {"iss": tenant.issuer, "sub": "x"}, key="", algorithm="none", headers={"kid": "test-key"}
    )
    response = await client.get("/v1/tenant", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_suspended_tenant_is_refused(
    client: httpx.AsyncClient, signer: Signer, tenant_factory: TenantFactory, owner_engine: object
) -> None:
    from sentinel_core.db import apply_context
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(owner_engine, AsyncEngine)
    tenant = await tenant_factory()
    async with owner_engine.connect() as conn, conn.begin():
        await apply_context(conn, tenant.ctx())
        await conn.execute(text("UPDATE core.tenant SET status = 'suspended'"))
    response = await client.get("/v1/tenant", headers=signer.headers(tenant))
    assert response.status_code == 403


# Tenant and branches -------------------------------------------------------------------------


async def test_tenant_endpoint_returns_only_the_callers_tenant(
    client: httpx.AsyncClient, signer: Signer, tenant_factory: TenantFactory
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    body = (await client.get("/v1/tenant", headers=signer.headers(tenant_a, ("analyst",)))).json()
    assert body["id"] == str(tenant_a.id)
    assert body["id"] != str(tenant_b.id)


async def test_branch_creation_needs_tenant_admin_and_is_audited(
    client: httpx.AsyncClient, signer: Signer, tenant: Tenant
) -> None:
    payload = {"code": "BR900", "name": "New Branch"}
    denied = await client.post(
        "/v1/branches", json=payload, headers=signer.headers(tenant, ("analyst",))
    )
    assert denied.status_code == 403
    created = await client.post("/v1/branches", json=payload, headers=signer.headers(tenant))
    assert created.status_code == 201
    assert created.json()["status"] == "active"
    duplicate = await client.post("/v1/branches", json=payload, headers=signer.headers(tenant))
    assert duplicate.status_code == 409
    audit = await client.get(
        "/v1/audit/events",
        params={"action": "branch.create"},
        headers=signer.headers(tenant, ("auditor",)),
    )
    assert [e["details"]["code"] for e in audit.json()["items"]] == ["BR900"]


async def test_branch_list_paginates_and_respects_branch_scope(
    client: httpx.AsyncClient, signer: Signer, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory(("BR001", "BR002", "BR003"))
    headers = signer.headers(tenant, ("analyst",))
    first = (await client.get("/v1/branches", params={"limit": 2}, headers=headers)).json()
    assert [b["code"] for b in first["items"]] == ["BR001", "BR002"]
    second = (
        await client.get(
            "/v1/branches", params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers
        )
    ).json()
    assert [b["code"] for b in second["items"]] == ["BR003"]
    assert second["next_cursor"] is None
    scoped = signer.headers(tenant, ("analyst",), branch_ids=(tenant.branches["BR002"],))
    assert [
        b["code"] for b in (await client.get("/v1/branches", headers=scoped)).json()["items"]
    ] == ["BR002"]


async def test_invalid_input_is_a_problem_document_without_echoing_values(
    client: httpx.AsyncClient, signer: Signer, tenant: Tenant
) -> None:
    response = await client.post(
        "/v1/branches",
        json={"code": "", "name": "SECRET-VALUE" * 40},
        headers=signer.headers(tenant),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM)
    assert {e["field"] for e in response.json()["errors"]} == {"code", "name"}
    assert "SECRET-VALUE" not in response.text
    assert (
        await client.get("/v1/branches", params={"cursor": "%%%"}, headers=signer.headers(tenant))
    ).status_code == 422


# Ingestion, quarantine, reveal, audit ---------------------------------------------------------


async def test_batch_upload_flow(
    client: httpx.AsyncClient, signer: Signer, platform: Platform, tenant: Tenant
) -> None:
    headers = signer.headers(tenant, ("integration",), service=True, subject="cbs-connector")
    rows = [party_row(1), party_row(2, email="broken"), party_row(3)]
    accepted = await _upload(client, headers, "parties", to_csv(PARTY_HEADER, rows))
    assert accepted.status_code == 202
    batch_id = accepted.json()["id"]
    assert accepted.json()["status"] == "received"
    await _drain(platform)
    job = (await client.get(f"/v1/ingestion/batches/{batch_id}", headers=headers)).json()
    assert (job["status"], job["records_loaded"], job["records_quarantined"]) == (
        "completed_with_errors",
        2,
        1,
    )

    quarantine = await client.get(
        "/v1/ingestion/quarantine", params={"batch_id": batch_id}, headers=headers
    )
    items = quarantine.json()["items"]
    assert [(i["row_number"], i["errors"][0]["field"]) for i in items] == [(3, "email")]
    assert "broken" not in quarantine.text
    assert "Test Person" not in quarantine.text

    repeat = await _upload(client, headers, "parties", to_csv(PARTY_HEADER, rows))
    assert repeat.json()["id"] == batch_id


async def test_upload_rules(
    client: httpx.AsyncClient, signer: Signer, platform: Platform, tenant: Tenant
) -> None:
    admin = signer.headers(tenant)
    assert (
        await _upload(client, signer.headers(tenant, ("analyst",)), "parties", b"x")
    ).status_code == 403
    assert (await _upload(client, admin, "payroll", b"x")).status_code == 422
    assert (await _upload(client, admin, "parties", b"")).status_code == 422
    scoped = signer.headers(tenant, branch_ids=(tenant.branches["BR001"],))
    assert (await _upload(client, scoped, "parties", b"x")).status_code == 403
    too_big = b"x" * (platform.settings.max_batch_bytes + 1)
    assert (await _upload(client, admin, "parties", too_big)).status_code == 413
    other = await client.get(f"/v1/ingestion/batches/{uuid4()}", headers=admin)
    assert other.status_code == 404


async def test_a_tenant_cannot_read_another_tenants_batch(
    client: httpx.AsyncClient, signer: Signer, tenant_factory: TenantFactory
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    created = await _upload(
        client, signer.headers(tenant_a), "parties", to_csv(PARTY_HEADER, [party_row(1)])
    )
    response = await client.get(
        f"/v1/ingestion/batches/{created.json()['id']}", headers=signer.headers(tenant_b)
    )
    assert response.status_code == 404


async def test_http_events_endpoint(
    client: httpx.AsyncClient, signer: Signer, platform: Platform, tenant: Tenant
) -> None:
    admin = signer.headers(tenant)
    await _upload(client, admin, "parties", to_csv(PARTY_HEADER, [party_row(1)]))
    await _drain(platform)
    await _upload(client, admin, "accounts", to_csv(ACCOUNT_HEADER, [account_row(1)]))
    await _drain(platform)
    events = [txn(1), txn(2, direction="sideways"), txn(3, account=99), txn(1)]
    response = await client.post("/v1/ingestion/events", json={"events": events}, headers=admin)
    assert response.status_code == 202
    assert response.json() == {"accepted": 1, "quarantined": 2, "duplicates": 1}
    too_many = await client.post(
        "/v1/ingestion/events", json={"events": [txn(i) for i in range(501)]}, headers=admin
    )
    assert too_many.status_code == 422


async def test_reveal_requires_role_purpose_and_is_audited(
    client: httpx.AsyncClient, signer: Signer, platform: Platform, tenant: Tenant
) -> None:
    token = await platform.tokeniser.tokenise(tenant.ctx(), "pan", "ABCPT4321Z")
    body = {"token": token, "purpose": "fraud_prevention", "reason": "confirm identity for case"}
    assert (
        await client.post("/v1/pii/reveal", json=body, headers=signer.headers(tenant, ("analyst",)))
    ).status_code == 403
    investigator = signer.headers(tenant, ("investigator",), subject="investigator-7")
    ok = await client.post("/v1/pii/reveal", json=body, headers=investigator)
    assert ok.status_code == 200
    assert ok.json() == {"value": "ABCPT4321Z", "field_type": "pan"}
    assert ok.headers["cache-control"] == "no-store"
    wrong_purpose = await client.post(
        "/v1/pii/reveal", json={**body, "purpose": "service_operations"}, headers=investigator
    )
    assert wrong_purpose.status_code == 403
    assert (
        await client.post(
            "/v1/pii/reveal", json={**body, "purpose": "marketing"}, headers=investigator
        )
    ).status_code == 422
    assert (
        await client.post(
            "/v1/pii/reveal", json={**body, "token": "ABCPT4321Z"}, headers=investigator
        )
    ).status_code == 422
    assert (
        await client.post("/v1/pii/reveal", json={**body, "reason": ""}, headers=investigator)
    ).status_code == 422

    audit = await client.get(
        "/v1/audit/events",
        params={"actor_id": "investigator-7"},
        headers=signer.headers(tenant, ("auditor",)),
    )
    assert [(e["action"], e["purpose"], e["actor_type"]) for e in audit.json()["items"]] == [
        ("pii.reveal", "fraud_prevention", "user"),
        ("pii.reveal.denied", "service_operations", "user"),
    ]
    assert "ABCPT4321Z" not in audit.text


async def test_audit_endpoints_need_audit_roles_and_verify_the_chain(
    client: httpx.AsyncClient, signer: Signer, tenant: Tenant
) -> None:
    await client.post(
        "/v1/branches", json={"code": "BRA", "name": "A"}, headers=signer.headers(tenant)
    )
    assert (
        await client.get("/v1/audit/events", headers=signer.headers(tenant, ("analyst",)))
    ).status_code == 403
    assert (
        await client.get(
            "/v1/audit/verify", headers=signer.headers(tenant, ("compliance_officer",))
        )
    ).status_code == 403
    auditor = signer.headers(tenant, ("auditor",))
    page = (await client.get("/v1/audit/events", params={"limit": 1}, headers=auditor)).json()
    assert [e["sequence"] for e in page["items"]] == [1]
    rest = (
        await client.get(
            "/v1/audit/events", params={"cursor": page["next_cursor"]}, headers=auditor
        )
    ).json()
    assert [e["sequence"] for e in rest["items"]] == [2]
    assert rest["items"][0]["previous_hash"] == page["items"][0]["hash"]
    verified = (await client.get("/v1/audit/verify", headers=auditor)).json()
    assert verified == {
        "verified": True,
        "records_checked": 2,
        "first_break_sequence": None,
        "last_anchor_at": None,
    }


async def test_health_endpoint_needs_no_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
