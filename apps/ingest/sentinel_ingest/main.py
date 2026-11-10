"""Worker process. Runs any of: batch processor, stream intake consumer, outbox relay."""

import argparse
import asyncio
import signal

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sentinel_core.config import get_settings
from sentinel_core.logging import configure_logging, get_logger
from sentinel_core.outbox import KafkaPublisher, OutboxRelay
from sentinel_core.runtime import Platform, build_platform
from sentinel_ingestion.public import BatchService, IngestionService, IntakeMessage, handle_intake
from sentinel_txn_fraud.public import (
    CONSUMER_GROUP,
    TRANSACTION_TOPIC,
    BacktestService,
    ScoringService,
    build_state,
    handle_transactions,
)

log = get_logger(__name__)
ROLES = ("batches", "intake", "relay", "scoring", "backtests")


async def run_batches(
    batches: BatchService, stop: asyncio.Event, idle_seconds: float = 1.0
) -> None:
    while not stop.is_set():
        try:
            claimed = await batches.claim_next()
            if claimed is not None:
                job = await batches.process(*claimed)
                log.info(
                    "batch_processed",
                    batch_id=str(job.id),
                    status=job.status,
                    loaded=job.records_loaded,
                    quarantined=job.records_quarantined,
                )
                continue
        except Exception:
            log.exception("batch_loop_failed")
        await _wait(stop, idle_seconds)


async def run_intake(platform: Platform, service: IngestionService, stop: asyncio.Event) -> None:
    consumer = AIOKafkaConsumer(
        platform.settings.intake_topic,
        bootstrap_servers=platform.settings.kafka_bootstrap,
        group_id="sentinel-ingest",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        while not stop.is_set():
            polled = await consumer.getmany(timeout_ms=500, max_records=500)
            messages = [
                IntakeMessage(record.value or b"", list(record.headers))
                for records in polled.values()
                for record in records
            ]
            if not messages:
                continue
            counts = await handle_intake(service, messages)
            # Offsets are committed only after the database transaction, so a crash replays
            # the poll; the unique key on transactions makes the replay harmless.
            await consumer.commit()
            log.info(
                "intake_processed",
                total=counts.total,
                loaded=counts.loaded,
                quarantined=counts.quarantined,
                duplicates=counts.duplicates,
            )
    finally:
        await consumer.stop()


async def run_scoring(platform: Platform, service: ScoringService, stop: asyncio.Event) -> None:
    """Score every ingested transaction. Offsets are committed after the scores are stored; a
    replayed poll is recognised by transaction id and skipped."""
    consumer = AIOKafkaConsumer(
        TRANSACTION_TOPIC,
        bootstrap_servers=platform.settings.kafka_bootstrap,
        group_id=CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        while not stop.is_set():
            polled = await consumer.getmany(timeout_ms=500, max_records=500)
            # Partitions are handled one after another, in offset order, so each account's
            # transactions are scored in sequence.
            values = [record.value or b"" for records in polled.values() for record in records]
            if not values:
                continue
            counts = await handle_transactions(service, values)
            await consumer.commit()
            log.info("scoring_processed", **counts)
    finally:
        await consumer.stop()


async def run_backtests(
    backtests: BacktestService, stop: asyncio.Event, idle_seconds: float = 1.0
) -> None:
    while not stop.is_set():
        try:
            claimed = await backtests.claim_next()
            if claimed is not None:
                job = await backtests.run(*claimed)
                log.info(
                    "backtest_finished", backtest_id=str(job.id), status=job.status, hits=job.hits
                )
                continue
        except Exception:
            log.exception("backtest_loop_failed")
        await _wait(stop, idle_seconds)


async def run_relay(platform: Platform, stop: asyncio.Event) -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=platform.settings.kafka_bootstrap, acks="all", enable_idempotence=True
    )
    await producer.start()
    try:
        await OutboxRelay(platform.db, KafkaPublisher(producer.send_and_wait)).run_forever(stop)
    finally:
        await producer.stop()


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return


async def run(roles: list[str]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    platform = build_platform(settings)
    service = IngestionService(platform)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    tasks = []
    if "batches" in roles:
        tasks.append(asyncio.create_task(run_batches(BatchService(platform, service), stop)))
    if "intake" in roles:
        tasks.append(asyncio.create_task(run_intake(platform, service, stop)))
    if "relay" in roles:
        tasks.append(asyncio.create_task(run_relay(platform, stop)))
    if "scoring" in roles:
        scoring = ScoringService(platform, build_state(settings))
        tasks.append(asyncio.create_task(run_scoring(platform, scoring, stop)))
    if "backtests" in roles:
        tasks.append(asyncio.create_task(run_backtests(BacktestService(platform), stop)))
    log.info("worker_started", roles=roles)
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await platform.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="sentinel-ingest")
    parser.add_argument(
        "--roles", default=",".join(ROLES), help="comma-separated: " + ", ".join(ROLES)
    )
    args = parser.parse_args()
    roles = [r for r in args.roles.split(",") if r]
    unknown = set(roles) - set(ROLES)
    if unknown:
        parser.error(f"unknown roles: {', '.join(sorted(unknown))}")
    asyncio.run(run(roles))
