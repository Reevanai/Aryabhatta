"""Event envelope construction and schema validation."""

import json
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator

from sentinel_core.context import TenantContext
from sentinel_core.ids import uuid7

_SCHEMA_FILES = {
    "ingestion.transaction.received": "transaction.received.schema.json",
    "txn_fraud.score.computed": "score.computed.schema.json",
    "cases.case.updated": "case.updated.schema.json",
    "cases.decision.recorded": "decision.recorded.schema.json",
}


def _load(name: str) -> dict[str, Any]:
    path = resources.files("sentinel_core") / "schemas" / "events" / name
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


@cache
def _envelope_validator() -> Draft202012Validator:
    return Draft202012Validator(
        _load("envelope.schema.json"), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


@cache
def _payload_validator(event_type: str) -> Draft202012Validator:
    name = _SCHEMA_FILES.get(event_type)
    if name is None and event_type.endswith(".alert.raised"):
        name = "alert.raised.schema.json"
    if name is None:
        raise KeyError(f"no schema registered for event type {event_type}")
    return Draft202012Validator(_load(name), format_checker=Draft202012Validator.FORMAT_CHECKER)


def build_event(
    ctx: TenantContext,
    *,
    event_type: str,
    producer: str,
    payload: dict[str, Any],
    branch_id: UUID | None = None,
    schema_version: str = "1.0",
) -> dict[str, Any]:
    """Build an envelope and validate it and its payload. Raises on any schema violation."""
    event: dict[str, Any] = {
        "event_id": str(uuid7()),
        "event_type": event_type,
        "schema_version": schema_version,
        "tenant_id": str(ctx.tenant_id),
        "branch_id": str(branch_id) if branch_id else None,
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": producer,
        "purpose": ctx.purpose,
        "payload": payload,
    }
    validate_event(event)
    return event


def validate_event(event: dict[str, Any]) -> None:
    _envelope_validator().validate(event)
    _payload_validator(event["event_type"]).validate(event["payload"])
