"""Port for the service that holds each tenant's key-encryption key."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class WrappedKey:
    ciphertext: bytes
    kek_ref: str


class KeyService(Protocol):
    """Wraps and unwraps data keys under a per-tenant key-encryption key.

    The key-encryption key never leaves the service.
    """

    async def ensure_tenant_key(self, tenant_id: UUID) -> None: ...

    async def wrap(self, tenant_id: UUID, data_key: bytes) -> WrappedKey: ...

    async def unwrap(self, tenant_id: UUID, wrapped: WrappedKey) -> bytes: ...

    async def destroy_tenant_key(self, tenant_id: UUID) -> None:
        """Cryptographic erasure: after this, the tenant's data keys cannot be unwrapped."""
        ...
