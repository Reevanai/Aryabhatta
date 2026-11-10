"""Builds the shared platform services from configuration."""

import base64
from dataclasses import dataclass

from sentinel_core.config import Settings
from sentinel_core.crypto import KeyService, LocalKeyService, OpenBaoKeyService
from sentinel_core.db import Database
from sentinel_core.filestore import FileStore, LocalFileStore
from sentinel_core.tenant_keys import TenantKeys
from sentinel_core.tokenisation import Tokeniser


@dataclass
class Platform:
    settings: Settings
    db: Database
    key_service: KeyService
    keys: TenantKeys
    tokeniser: Tokeniser
    files: FileStore

    async def aclose(self) -> None:
        await self.db.dispose()
        if isinstance(self.key_service, OpenBaoKeyService):
            await self.key_service.aclose()


def build_key_service(settings: Settings) -> KeyService:
    if settings.key_service == "openbao":
        if not settings.openbao_addr or settings.openbao_token is None:
            raise RuntimeError("SENTINEL_OPENBAO_ADDR and SENTINEL_OPENBAO_TOKEN are required")
        return OpenBaoKeyService(
            settings.openbao_addr,
            settings.openbao_token.get_secret_value(),
            settings.openbao_transit_mount,
        )
    if settings.local_master_key is None:
        raise RuntimeError("SENTINEL_LOCAL_MASTER_KEY is required for the local key service")
    return LocalKeyService(base64.b64decode(settings.local_master_key.get_secret_value()))


def build_platform(settings: Settings, key_service: KeyService | None = None) -> Platform:
    db = Database(
        settings.database_url_app, settings.database_url_vault, settings.database_url_relay
    )
    key_service = key_service or build_key_service(settings)
    keys = TenantKeys(db, key_service)
    return Platform(
        settings=settings,
        db=db,
        key_service=key_service,
        keys=keys,
        tokeniser=Tokeniser(db, keys),
        files=LocalFileStore(settings.filestore_dir, keys),
    )
