"""Structured JSON logging with a scrubber that removes personal data.

Code should log tokens, never clear values. The scrubber is a second line of defence.
"""

import logging
import re
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "[redacted]"

_SENSITIVE_KEYS = re.compile(
    r"(name|pan|aadhaar|phone|mobile|email|address|dob|birth|account_number|vpa|password|secret|"
    r"token_value|authorization|ip_address|device_id|value|raw)",
    re.IGNORECASE,
)
_PATTERNS = (
    re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),  # PAN format
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b[0-9]{4}\s?[0-9]{4}\s?[0-9]{4}\b"),  # 12-digit identifier
    re.compile(r"(?<![0-9])(?:\+91[\s-]?)?[6-9][0-9]{9}(?![0-9])"),  # Indian mobile number
    re.compile(r"\b[0-9]{9,18}\b"),  # account-number-like digit runs
)
_SAFE_KEYS = frozenset(
    {"event", "level", "timestamp", "logger", "tenant_id", "trace_id", "actor_id"}
)


_KEEP = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|tok_[a-z]+_[A-Za-z0-9_-]{16,})"
)


def scrub_text(text: str) -> str:
    """Redact identifier-like runs, leaving UUIDs and tokens intact."""
    parts = _KEEP.split(text)
    for index in range(0, len(parts), 2):
        for pattern in _PATTERNS:
            parts[index] = pattern.sub(REDACTED, parts[index])
    return "".join(parts)


def _scrub(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(key, v) for v in value]
    if key not in _SAFE_KEYS and _SENSITIVE_KEYS.search(key):
        return value if isinstance(value, str) and value.startswith("tok_") else REDACTED
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        event_dict[key] = _scrub(key, event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            scrub_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
