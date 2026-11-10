"""Transactional outbox: events are written with the state change, then relayed to Kafka."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sentinel_core.db import Database
from sentinel_core.logging import get_logger

log = get_logger(__name__)


async def add_to_outbox(
    conn: AsyncConnection, *, topic: str, key: str, event: dict[str, Any]
) -> None:
    """Queue a validated event in the caller's transaction."""
    await conn.execute(
        text(
            "INSERT INTO core.outbox (id, tenant_id, topic, key, event) "
            "VALUES (:id, :t, :topic, :key, CAST(:event AS jsonb))"
        ),
        {
            "id": event["event_id"],
            "t": event["tenant_id"],
            "topic": topic,
            "key": key,
            "event": json.dumps(event),
        },
    )


class Publisher(Protocol):
    async def publish(
        self, topic: str, key: bytes, value: bytes, headers: list[tuple[str, bytes]]
    ) -> None: ...


class OutboxRelay:
    """Publishes unpublished outbox rows in creation order, then marks them published.

    Delivery is at least once: a crash after publishing and before the update republishes the
    row, so consumers deduplicate on `event_id`.
    """

    def __init__(self, db: Database, publisher: Publisher, batch_size: int = 200) -> None:
        self._db = db
        self._publisher = publisher
        self._batch_size = batch_size

    async def run_once(self) -> int:
        async with self._db.relay_tx() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, tenant_id, topic, key, event FROM core.outbox "
                        "WHERE published_at IS NULL ORDER BY created_at, id "
                        "LIMIT :n FOR UPDATE SKIP LOCKED"
                    ),
                    {"n": self._batch_size},
                )
            ).all()
            for row in rows:
                await self._publisher.publish(
                    row.topic,
                    row.key.encode(),
                    json.dumps(row.event, separators=(",", ":")).encode(),
                    [("tenant_id", str(row.tenant_id).encode())],
                )
            if rows:
                await conn.execute(
                    text("UPDATE core.outbox SET published_at = now() WHERE id = ANY(:ids)"),
                    {"ids": [row.id for row in rows]},
                )
            return len(rows)

    async def run_forever(self, stop: asyncio.Event, idle_seconds: float = 0.5) -> None:
        while not stop.is_set():
            try:
                published = await self.run_once()
            except Exception:
                log.exception("outbox_relay_failed")
                published = 0
            if published == 0:
                await _sleep_or_stop(stop, idle_seconds)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return


class KafkaPublisher:
    """Publisher on an aiokafka producer."""

    def __init__(self, send_and_wait: Callable[..., Awaitable[Any]]) -> None:
        self._send = send_and_wait

    async def publish(
        self, topic: str, key: bytes, value: bytes, headers: list[tuple[str, bytes]]
    ) -> None:
        await self._send(topic, value=value, key=key, headers=headers)
