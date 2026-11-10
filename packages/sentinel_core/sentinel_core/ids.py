"""Time-ordered identifiers (UUID version 7)."""

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    millis = time.time_ns() // 1_000_000
    raw = bytearray(millis.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))
