import pytest
from sentinel_core.errors import Forbidden, NotFound
from sentinel_core.runtime import Platform
from sqlalchemy import text

from tests.conftest import Tenant, TenantFactory

pytestmark = pytest.mark.integration


async def test_tokenise_and_reveal_round_trip(platform: Platform, tenant: Tenant) -> None:
    token = await platform.tokeniser.tokenise(tenant.ctx(), "pan", "ABCPT1234Z")
    assert "ABCPT1234Z" not in token
    revealed = await platform.tokeniser.reveal(tenant.ctx("kyc"), token, "test reason")
    assert (revealed.value, revealed.field_type) == ("ABCPT1234Z", "pan")


async def test_same_value_same_token_within_a_tenant_and_different_across_tenants(
    platform: Platform, tenant_factory: TenantFactory
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    first = await platform.tokeniser.tokenise(tenant_a.ctx(), "phone", "9000000001")
    again = await platform.tokeniser.tokenise(tenant_a.ctx(), "phone", "+91 9000000001")
    other = await platform.tokeniser.tokenise(tenant_b.ctx(), "phone", "9000000001")
    assert first == again
    assert first != other


async def test_a_tenant_cannot_reveal_another_tenants_token(
    platform: Platform, tenant_factory: TenantFactory
) -> None:
    tenant_a, tenant_b = await tenant_factory(), await tenant_factory()
    token = await platform.tokeniser.tokenise(tenant_a.ctx(), "email", "a@example.invalid")
    with pytest.raises(NotFound):
        await platform.tokeniser.reveal(tenant_b.ctx("kyc"), token, "probing")


async def test_stored_value_is_encrypted(platform: Platform, tenant: Tenant) -> None:
    token = await platform.tokeniser.tokenise(tenant.ctx(), "name", "Test Person Encrypted")
    async with platform.db.vault_tx(tenant.ctx()) as conn:
        blob = (
            await conn.execute(
                text("SELECT ciphertext FROM vault.pii_token WHERE token = :t"), {"t": token}
            )
        ).scalar_one()
    assert b"Test Person" not in bytes(blob)


async def test_reveal_is_refused_for_a_purpose_the_field_does_not_allow(
    platform: Platform, tenant: Tenant
) -> None:
    # PAN is catalogued for fraud, AML and KYC purposes, not for security monitoring.
    token = await platform.tokeniser.tokenise(tenant.ctx(), "pan", "ABCPT9999Z")
    with pytest.raises(Forbidden):
        await platform.tokeniser.reveal(tenant.ctx("security_monitoring"), token, "curious")
    await platform.tokeniser.reveal(tenant.ctx("fraud_prevention"), token, "case review")


async def test_every_reveal_attempt_is_audited_without_the_value(
    platform: Platform, tenant: Tenant
) -> None:
    token = await platform.tokeniser.tokenise(tenant.ctx(), "pan", "ABCPT7777Z")
    await platform.tokeniser.reveal(tenant.ctx("kyc"), token, "verify identity")
    with pytest.raises(Forbidden):
        await platform.tokeniser.reveal(tenant.ctx("service_operations"), token, "no good reason")
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT action, purpose, details::text AS details FROM core.audit_event "
                    "WHERE resource = :r ORDER BY sequence"
                ),
                {"r": f"token:{token}"},
            )
        ).all()
    assert [(r.action, r.purpose) for r in rows] == [
        ("pii.reveal", "kyc"),
        ("pii.reveal.denied", "service_operations"),
    ]
    assert all("ABCPT7777Z" not in r.details for r in rows)
    assert "verify identity" in rows[0].details


async def test_token_for_computes_without_storing(platform: Platform, tenant: Tenant) -> None:
    token = await platform.tokeniser.token_for(tenant.ctx(), "acct", "555000111")
    with pytest.raises(NotFound):
        await platform.tokeniser.reveal(tenant.ctx("fraud_prevention"), token, "lookup")


async def test_data_keys_are_stored_wrapped(platform: Platform, tenant: Tenant) -> None:
    key = await platform.keys.get(tenant.ctx(), "pii")
    async with platform.db.tenant_tx(tenant.ctx()) as conn:
        rows = (await conn.execute(text("SELECT purpose, wrapped_key FROM core.tenant_key"))).all()
    assert {r.purpose for r in rows} == {"pii", "hmac", "file", "audit"}
    assert all(key.key not in bytes(r.wrapped_key) for r in rows)


async def test_destroying_the_tenant_key_makes_pii_unreadable(
    platform: Platform, tenant_factory: TenantFactory
) -> None:
    tenant = await tenant_factory()
    token = await platform.tokeniser.tokenise(tenant.ctx(), "pan", "ABCPT5555Z")
    await platform.key_service.destroy_tenant_key(tenant.id)
    platform.keys.forget(tenant.id)
    with pytest.raises(KeyError):
        await platform.tokeniser.reveal(tenant.ctx("kyc"), token, "after offboarding")
