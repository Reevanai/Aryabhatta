"""Stream intake: transactions arriving on the Kafka intake topic.

Message value: a transaction as JSON (same shape as the HTTP endpoint). Header `tenant_id`
names the tenant.

Trust boundary: whoever can write to the intake topic can submit transactions for the tenant
named in the header. Topic write access must be restricted per tenant by broker ACLs; that is
deployment configuration.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sentinel_core.context import TenantContext
from sentinel_core.logging import get_logger

from sentinel_ingestion.service import Counts, IngestionService

log = get_logger(__name__)


@dataclass(frozen=True)
class IntakeMessage:
    value: bytes
    headers: Sequence[tuple[str, bytes]]


def _tenant(headers: Sequence[tuple[str, bytes]]) -> UUID | None:
    for name, value in headers:
        if name == "tenant_id":
            try:
                return UUID(value.decode())
            except (ValueError, UnicodeDecodeError):
                return None
    return None


async def handle_intake(service: IngestionService, messages: Sequence[IntakeMessage]) -> Counts:
    """Ingest a poll's worth of messages, grouped by tenant. Bad rows are quarantined.

    Messages without a usable tenant header cannot be attributed, so they are counted and
    dropped; their content is not logged.
    """
    by_tenant: dict[UUID, list[tuple[int | None, dict[str, Any]]]] = {}
    unattributed = 0
    for message in messages:
        tenant_id = _tenant(message.headers)
        if tenant_id is None:
            unattributed += 1
            continue
        try:
            raw = json.loads(message.value)
            if not isinstance(raw, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            raw = {"__unparseable__": "present"}
        by_tenant.setdefault(tenant_id, []).append((None, raw))
    if unattributed:
        log.warning("intake_unattributed_messages", count=unattributed)
    total = Counts()
    for tenant_id, rows in by_tenant.items():
        ctx = TenantContext(tenant_id, "stream-intake")
        total.add(await service.ingest_rows(ctx, "transactions", rows, source="stream"))
    return total
