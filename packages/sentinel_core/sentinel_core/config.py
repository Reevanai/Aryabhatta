"""Runtime configuration, read from SENTINEL_* environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", extra="ignore")

    database_url_app: str
    database_url_vault: str
    database_url_relay: str | None = None
    database_url_owner: str | None = None

    key_service: Literal["local", "openbao"] = "local"
    local_master_key: SecretStr | None = None
    openbao_addr: str | None = None
    openbao_token: SecretStr | None = None
    openbao_transit_mount: str = "transit"

    kafka_bootstrap: str = "localhost:9092"
    valkey_url: str | None = None
    intake_topic: str = "sentinel.intake.transaction.v1"
    filestore_dir: Path = Path("var/files")
    max_batch_bytes: int = 50 * 1024 * 1024
    oidc_audience: str = "sentinel-api"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
