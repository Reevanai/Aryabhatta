"""Opaque keyset cursors."""

import base64
import json
from typing import Any

from sentinel_core.errors import Invalid


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None, length: int) -> list[Any] | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Invalid("invalid cursor") from exc
    if not isinstance(values, list) or len(values) != length:
        raise Invalid("invalid cursor")
    return values
