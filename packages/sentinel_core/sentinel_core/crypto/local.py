"""Key service for development and tests. Not for production.

Derives each tenant's key-encryption key from one master key held in configuration.
Production uses OpenBao (or the bank's own KMS) so that the key never sits in app memory.
"""

from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from sentinel_core.crypto.envelope import decrypt, encrypt
from sentinel_core.crypto.keys import WrappedKey


class LocalKeyService:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._master = master_key
        self._destroyed: set[UUID] = set()

    def _kek(self, tenant_id: UUID) -> bytes:
        if tenant_id in self._destroyed:
            raise KeyError(f"key for tenant {tenant_id} was destroyed")
        hkdf = HKDF(hashes.SHA256(), 32, salt=None, info=b"sentinel-kek:" + tenant_id.bytes)
        return hkdf.derive(self._master)

    async def ensure_tenant_key(self, tenant_id: UUID) -> None:
        self._kek(tenant_id)

    async def wrap(self, tenant_id: UUID, data_key: bytes) -> WrappedKey:
        blob = encrypt(self._kek(tenant_id), data_key, tenant_id.bytes)
        return WrappedKey(blob, f"local:{tenant_id}")

    async def unwrap(self, tenant_id: UUID, wrapped: WrappedKey) -> bytes:
        return decrypt(self._kek(tenant_id), wrapped.ciphertext, tenant_id.bytes)

    async def destroy_tenant_key(self, tenant_id: UUID) -> None:
        self._destroyed.add(tenant_id)
