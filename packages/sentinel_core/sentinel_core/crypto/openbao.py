"""Key service backed by the transit secrets engine of OpenBao (Vault-compatible HTTP API).

One transit key per tenant, named `tenant-<uuid>`. Data keys are wrapped with
`transit/encrypt` and unwrapped with `transit/decrypt`.

Limit: authenticates with a static token. Production should use a short-lived token from an
auth method such as AppRole or Kubernetes; that is deployment work for the Helm phase.
"""

import base64
from uuid import UUID

import httpx

from sentinel_core.crypto.keys import WrappedKey


class OpenBaoKeyService:
    def __init__(
        self, addr: str, token: str, mount: str = "transit", client: httpx.AsyncClient | None = None
    ) -> None:
        self._mount = mount
        self._client = client or httpx.AsyncClient(
            base_url=addr.rstrip("/"), headers={"X-Vault-Token": token}, timeout=5.0
        )

    @staticmethod
    def _name(tenant_id: UUID) -> str:
        return f"tenant-{tenant_id}"

    async def _post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        response = await self._client.post(f"/v1/{self._mount}/{path}", json=body)
        response.raise_for_status()
        if not response.content:
            return {}
        data: dict[str, object] = response.json().get("data") or {}
        return data

    async def ensure_tenant_key(self, tenant_id: UUID) -> None:
        await self._post(f"keys/{self._name(tenant_id)}", {"type": "aes256-gcm96"})

    async def wrap(self, tenant_id: UUID, data_key: bytes) -> WrappedKey:
        data = await self._post(
            f"encrypt/{self._name(tenant_id)}",
            {"plaintext": base64.b64encode(data_key).decode()},
        )
        return WrappedKey(str(data["ciphertext"]).encode(), f"openbao:{self._name(tenant_id)}")

    async def unwrap(self, tenant_id: UUID, wrapped: WrappedKey) -> bytes:
        data = await self._post(
            f"decrypt/{self._name(tenant_id)}", {"ciphertext": wrapped.ciphertext.decode()}
        )
        return base64.b64decode(str(data["plaintext"]))

    async def destroy_tenant_key(self, tenant_id: UUID) -> None:
        name = self._name(tenant_id)
        await self._post(f"keys/{name}/config", {"deletion_allowed": True})
        response = await self._client.delete(f"/v1/{self._mount}/keys/{name}")
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
