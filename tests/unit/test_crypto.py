from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag
from sentinel_core.crypto import LocalKeyService, decrypt, encrypt, new_data_key


def test_round_trip() -> None:
    key = new_data_key()
    assert decrypt(key, encrypt(key, b"secret", b"ctx"), b"ctx") == b"secret"


def test_ciphertext_is_bound_to_its_context() -> None:
    key = new_data_key()
    with pytest.raises(InvalidTag):
        decrypt(key, encrypt(key, b"secret", b"tenant-a"), b"tenant-b")


def test_tampering_is_detected() -> None:
    key = new_data_key()
    blob = bytearray(encrypt(key, b"secret", b"ctx"))
    blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        decrypt(key, bytes(blob), b"ctx")


def test_nonces_are_not_reused() -> None:
    key = new_data_key()
    assert encrypt(key, b"same", b"ctx") != encrypt(key, b"same", b"ctx")


async def test_local_key_service_wraps_per_tenant() -> None:
    service = LocalKeyService(b"m" * 32)
    tenant_a, tenant_b = uuid4(), uuid4()
    data_key = new_data_key()
    wrapped = await service.wrap(tenant_a, data_key)
    assert data_key not in wrapped.ciphertext
    assert await service.unwrap(tenant_a, wrapped) == data_key
    with pytest.raises(InvalidTag):
        await service.unwrap(tenant_b, wrapped)


async def test_destroying_a_tenant_key_makes_its_data_keys_unusable() -> None:
    service = LocalKeyService(b"m" * 32)
    tenant = uuid4()
    wrapped = await service.wrap(tenant, new_data_key())
    await service.destroy_tenant_key(tenant)
    with pytest.raises(KeyError):
        await service.unwrap(tenant, wrapped)


def test_master_key_length_is_enforced() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        LocalKeyService(b"short")
