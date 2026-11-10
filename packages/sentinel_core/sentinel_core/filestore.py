"""Encrypted file storage for uploaded batches.

Files hold clear personal data on arrival, so they are encrypted with the tenant's file key
before they touch disk.

Limit: files are encrypted and decrypted in memory, so size is capped by configuration. A
local directory is the only adapter; an S3-API adapter belongs to the deployment phase.
"""

import hashlib
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sentinel_core.context import TenantContext
from sentinel_core.crypto import decrypt, encrypt
from sentinel_core.tenant_keys import TenantKeys


class FileStore(Protocol):
    async def put(self, ctx: TenantContext, file_id: UUID, data: bytes) -> str: ...

    async def get(self, ctx: TenantContext, ref: str) -> bytes: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalFileStore:
    def __init__(self, root: Path, keys: TenantKeys) -> None:
        self._root = root
        self._keys = keys

    async def put(self, ctx: TenantContext, file_id: UUID, data: bytes) -> str:
        key = await self._keys.get(ctx, "file")
        ref = f"{ctx.tenant_id}/{file_id}.v{key.version}"
        path = self._root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt(key.key, data, ref.encode()))
        return ref

    async def get(self, ctx: TenantContext, ref: str) -> bytes:
        tenant, _, name = ref.partition("/")
        if tenant != str(ctx.tenant_id) or "/" in name or ".." in name:
            raise PermissionError("file reference outside tenant")
        version = int(name.rsplit(".v", 1)[1])
        key = await self._keys.get(ctx, "file", version)
        return decrypt(key.key, (self._root / ref).read_bytes(), ref.encode())
