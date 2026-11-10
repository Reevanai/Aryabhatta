"""Key services and envelope encryption."""

from sentinel_core.crypto.envelope import decrypt, encrypt, new_data_key
from sentinel_core.crypto.keys import KeyService, WrappedKey
from sentinel_core.crypto.local import LocalKeyService
from sentinel_core.crypto.openbao import OpenBaoKeyService

__all__ = [
    "KeyService",
    "LocalKeyService",
    "OpenBaoKeyService",
    "WrappedKey",
    "decrypt",
    "encrypt",
    "new_data_key",
]
