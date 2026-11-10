"""AES-256-GCM encryption with data keys."""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE = 12


def new_data_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """Returns nonce || ciphertext || tag. `aad` binds the ciphertext to its context."""
    nonce = os.urandom(_NONCE)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[:_NONCE], blob[_NONCE:], aad)
