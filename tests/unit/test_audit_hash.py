from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sentinel_core.audit import anchor_signature, canonical_hash

BASE: dict[str, Any] = {
    "tenant_id": UUID(int=1),
    "sequence": 7,
    "event_id": UUID(int=2),
    "occurred_at": datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC),
    "actor_id": "user-1",
    "actor_type": "user",
    "action": "branch.create",
    "resource": "branch:x",
    "purpose": "service_operations",
    "details": {"b": 1, "a": "x"},
    "previous_hash": "0" * 64,
}


def test_hash_is_stable() -> None:
    assert canonical_hash(**BASE) == canonical_hash(**BASE)
    assert len(canonical_hash(**BASE)) == 64


def test_every_field_changes_the_hash() -> None:
    changes: dict[str, Any] = {
        "tenant_id": UUID(int=9),
        "sequence": 8,
        "event_id": UUID(int=3),
        "occurred_at": BASE["occurred_at"] + timedelta(microseconds=1),
        "actor_id": "user-2",
        "actor_type": "service",
        "action": "branch.delete",
        "resource": "branch:y",
        "purpose": "kyc",
        "details": {"b": 2, "a": "x"},
        "previous_hash": "1" * 64,
    }
    original = canonical_hash(**BASE)
    for name, value in changes.items():
        assert canonical_hash(**{**BASE, name: value}) != original, name


def test_detail_key_order_does_not_matter() -> None:
    assert canonical_hash(**{**BASE, "details": {"a": "x", "b": 1}}) == canonical_hash(**BASE)


def test_time_zone_representation_does_not_matter() -> None:
    ist = BASE["occurred_at"].astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert canonical_hash(**{**BASE, "occurred_at": ist}) == canonical_hash(**BASE)


def test_anchor_signature_depends_on_key_and_content() -> None:
    tenant = UUID(int=1)
    base = anchor_signature(b"k" * 32, tenant, 5, "h")
    assert anchor_signature(b"j" * 32, tenant, 5, "h") != base
    assert anchor_signature(b"k" * 32, tenant, 6, "h") != base
    assert anchor_signature(b"k" * 32, tenant, 5, "i") != base
