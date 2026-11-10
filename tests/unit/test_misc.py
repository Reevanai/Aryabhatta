from typing import Any
from uuid import UUID, uuid4

import pytest
from jsonschema import ValidationError
from sentinel_core.auth import PERMISSIONS, ROLES, principal_from_claims
from sentinel_core.context import TenantContext
from sentinel_core.errors import Forbidden, Invalid, Unauthenticated
from sentinel_core.events import build_event, validate_event
from sentinel_core.ids import uuid7
from sentinel_core.pagination import decode_cursor, encode_cursor

TENANT = uuid4()


def claims(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sub": "u1",
        "tenant_id": str(TENANT),
        "realm_access": {"roles": ["analyst", "offline_access"]},
    }
    base.update(over)
    return base


def test_principal_keeps_only_known_roles() -> None:
    principal = principal_from_claims(claims(), TENANT)
    assert principal.roles == frozenset({"analyst"})
    assert principal.actor_type == "user"
    assert principal.branch_ids == ()


def test_token_tenant_must_match_issuer_tenant() -> None:
    with pytest.raises(Unauthenticated):
        principal_from_claims(claims(), uuid4())


@pytest.mark.parametrize("bad", [{"tenant_id": "nope"}, {"tenant_id": None}, {"branch_ids": ["x"]}])
def test_malformed_tenant_or_branch_claims_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(Unauthenticated):
        principal_from_claims(claims(**bad), TENANT)


def test_service_accounts_are_recognised() -> None:
    assert principal_from_claims(claims(client_id="cbs"), TENANT).actor_type == "service"
    named = claims(preferred_username="service-account-cbs")
    assert principal_from_claims(named, TENANT).actor_type == "service"


def test_branch_scope_flows_into_the_context() -> None:
    branch = uuid4()
    principal = principal_from_claims(claims(branch_ids=[str(branch)]), TENANT)
    assert principal.context().branch_scope == (branch,)
    assert not principal.context().institution_wide


def test_permission_check() -> None:
    principal = principal_from_claims(claims(), TENANT)
    principal.require("branch:read")
    with pytest.raises(Forbidden):
        principal.require("branch:write")


def test_permissions_reference_only_known_roles_and_exclude_operators() -> None:
    for roles in PERMISSIONS.values():
        assert roles <= ROLES
    assert "platform_operator" not in ROLES


def test_unknown_purpose_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown purpose"):
        TenantContext(TENANT, "a", purpose="marketing")


def test_cursor_round_trip_and_rejection() -> None:
    assert decode_cursor(encode_cursor(["a", 2]), 2) == ["a", 2]
    assert decode_cursor(None, 2) is None
    for bad in ("!!!", encode_cursor(["only-one"])):
        with pytest.raises(Invalid):
            decode_cursor(bad, 2)


def test_uuid7_is_version_7_and_time_ordered() -> None:
    first, second = uuid7(), uuid7()
    assert first.version == 7
    assert UUID(str(first)) == first
    assert first.bytes[:6] <= second.bytes[:6]


def _payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "transaction_id": str(uuid7()),
        "channel": "upi",
        "direction": "debit",
        "amount": {"amount": "10.00", "currency": "INR"},
        "booked_at": "2026-01-01T00:00:00+00:00",
        "account_token": "tok_acct_" + "A" * 32,
        "status": "completed",
    }
    payload.update(over)
    return payload


def test_event_envelope_is_built_and_validated() -> None:
    event = build_event(
        TenantContext(TENANT, "svc"),
        event_type="ingestion.transaction.received",
        producer="ingestion",
        payload=_payload(),
    )
    assert event["tenant_id"] == str(TENANT)
    validate_event(event)


def test_event_with_a_clear_identifier_is_refused() -> None:
    with pytest.raises(ValidationError):
        build_event(
            TenantContext(TENANT, "svc"),
            event_type="ingestion.transaction.received",
            producer="ingestion",
            payload=_payload(account_token="001122330001"),
        )


def test_event_type_without_a_schema_is_refused() -> None:
    with pytest.raises(KeyError):
        build_event(TenantContext(TENANT, "svc"), event_type="made.up", producer="x", payload={})
