"""Stream scoring: every ingested transaction is scored from the event topic."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sentinel_core.context import TenantContext
from sentinel_core.logging import get_logger

from sentinel_txn_fraud.runtime import ScoringService

log = get_logger(__name__)
TRANSACTION_TOPIC = "sentinel.ingestion.transaction.v1"
CONSUMER_GROUP = "sentinel-scoring"


async def handle_transactions(service: ScoringService, values: Sequence[bytes]) -> dict[str, int]:
    """Score a poll's worth of `ingestion.transaction.received` events, grouped by tenant.

    Order within a tenant is preserved, which keeps each account's history in sequence.
    """
    by_tenant: dict[UUID, list[dict[str, Any]]] = {}
    skipped = 0
    for value in values:
        try:
            event = json.loads(value)
            tenant = UUID(event["tenant_id"])
            payload = event["payload"]
            if event["event_type"] != "ingestion.transaction.received":
                raise ValueError
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        by_tenant.setdefault(tenant, []).append(payload)
    if skipped:
        log.warning("scoring_skipped_unreadable_events", count=skipped)
    scored = flagged = 0
    for tenant, payloads in by_tenant.items():
        ctx = TenantContext(tenant, "stream-scoring", purpose="fraud_prevention")
        results = await service.score_events(ctx, payloads)
        scored += len(results)
        flagged += sum(1 for r in results if r.outcome.recommendation != "allow")
    return {"scored": scored, "flagged": flagged, "skipped": skipped}
