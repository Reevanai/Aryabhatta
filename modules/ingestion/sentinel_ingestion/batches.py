"""Batch file intake: store, queue, and process CSV files."""

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sentinel_core.audit import record_audit
from sentinel_core.context import TenantContext
from sentinel_core.errors import Invalid, NotFound, TooLarge
from sentinel_core.filestore import sha256_hex
from sentinel_core.ids import uuid7
from sentinel_core.logging import get_logger
from sentinel_core.runtime import Platform
from sqlalchemy import text

from sentinel_ingestion.domain.models import DATASETS, AccountRow, Dataset, PartyRow
from sentinel_ingestion.service import Counts, IngestionService

log = get_logger(__name__)
CHUNK_ROWS = 500
MAX_ATTEMPTS = 3
STALE_CLAIM = "10 minutes"

_TRANSACTION_COLUMNS = {
    "source_reference",
    "channel",
    "direction",
    "amount",
    "currency",
    "booked_at",
    "account_number",
    "branch_code",
    "counterparty_account_number",
    "counterparty_vpa",
    "counterparty_bank_code",
    "device_id",
    "ip_address",
    "merchant_category",
    "status",
}
_TRANSACTION_REQUIRED = {
    "source_reference",
    "channel",
    "direction",
    "amount",
    "booked_at",
    "account_number",
}


def _columns(dataset: Dataset) -> tuple[set[str], set[str]]:
    """(allowed, required) column names for a dataset's CSV."""
    if dataset == "transactions":
        return _TRANSACTION_COLUMNS, _TRANSACTION_REQUIRED
    model = PartyRow if dataset == "parties" else AccountRow
    allowed = set(model.model_fields)
    required = {name for name, info in model.model_fields.items() if info.is_required()}
    return allowed, required


@dataclass(frozen=True)
class BatchJob:
    id: UUID
    dataset: str
    status: str
    records_total: int
    records_loaded: int
    records_quarantined: int
    file_sha256: str
    created_at: datetime
    completed_at: datetime | None
    error: str | None

    @classmethod
    def from_row(cls, row: Any) -> "BatchJob":
        return cls(
            row.id,
            row.dataset,
            row.status,
            row.records_total,
            row.records_loaded,
            row.records_quarantined,
            row.file_sha256,
            row.created_at,
            row.completed_at,
            row.error,
        )


_JOB_COLUMNS = (
    "id, dataset, status, records_total, records_loaded, records_quarantined, file_sha256, "
    "created_at, completed_at, error"
)


class BatchService:
    def __init__(self, platform: Platform, ingestion: IngestionService) -> None:
        self._p = platform
        self._ingestion = ingestion

    async def create(self, ctx: TenantContext, dataset: str, data: bytes) -> tuple[BatchJob, bool]:
        """Store an uploaded file and queue it. Returns (job, created).

        The same file for the same dataset is accepted once: a repeat returns the first job.
        """
        if dataset not in DATASETS:
            raise Invalid(f"dataset must be one of {', '.join(DATASETS)}")
        if not data:
            raise Invalid("file is empty")
        if len(data) > self._p.settings.max_batch_bytes:
            raise TooLarge(f"file exceeds {self._p.settings.max_batch_bytes} bytes")
        digest = sha256_hex(data)
        async with self._p.db.tenant_tx(ctx) as conn:
            existing = (
                await conn.execute(
                    text(
                        f"SELECT {_JOB_COLUMNS} FROM ingestion.batch_job "
                        "WHERE dataset = :d AND file_sha256 = :h"
                    ),
                    {"d": dataset, "h": digest},
                )
            ).first()
        if existing:
            return BatchJob.from_row(existing), False
        batch_id = uuid7()
        file_ref = await self._p.files.put(ctx, batch_id, data)
        async with self._p.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "INSERT INTO ingestion.batch_job (id, tenant_id, dataset, status, file_ref, "
                        "file_sha256, created_by) VALUES (:id, :t, :d, 'received', :f, :h, :by) "
                        f"RETURNING {_JOB_COLUMNS}"
                    ),
                    {
                        "id": batch_id,
                        "t": ctx.tenant_id,
                        "d": dataset,
                        "f": file_ref,
                        "h": digest,
                        "by": ctx.actor_id,
                    },
                )
            ).one()
            await conn.execute(
                text("INSERT INTO ingestion.batch_queue (batch_id, tenant_id) VALUES (:b, :t)"),
                {"b": batch_id, "t": ctx.tenant_id},
            )
            await record_audit(
                conn,
                ctx,
                action="ingestion.batch.create",
                resource=f"batch:{batch_id}",
                details={"dataset": dataset, "file_sha256": digest, "bytes": len(data)},
            )
        return BatchJob.from_row(row), True

    async def get(self, ctx: TenantContext, batch_id: UUID) -> BatchJob:
        async with self._p.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_JOB_COLUMNS} FROM ingestion.batch_job WHERE id = :id"),
                    {"id": batch_id},
                )
            ).first()
        if row is None:
            raise NotFound("batch not found")
        return BatchJob.from_row(row)

    async def claim_next(self) -> tuple[UUID, UUID] | None:
        """Claim the oldest queued batch, or one whose worker stopped. Returns (tenant, batch)."""
        async with self._p.db.system_tx() as conn:
            await conn.execute(
                text(
                    "UPDATE ingestion.batch_queue SET status = 'dead' WHERE status = 'claimed' "
                    f"AND claimed_at < now() - interval '{STALE_CLAIM}' AND attempts >= :max"
                ),
                {"max": MAX_ATTEMPTS},
            )
            row = (
                await conn.execute(
                    text(
                        "UPDATE ingestion.batch_queue SET status = 'claimed', claimed_at = now(), "
                        "attempts = attempts + 1 WHERE batch_id = (SELECT batch_id FROM "
                        "ingestion.batch_queue WHERE status = 'queued' OR (status = 'claimed' AND "
                        f"claimed_at < now() - interval '{STALE_CLAIM}') ORDER BY enqueued_at "
                        "FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING tenant_id, batch_id"
                    )
                )
            ).first()
        return (row.tenant_id, row.batch_id) if row else None

    async def process(self, tenant_id: UUID, batch_id: UUID) -> BatchJob:
        """Validate and load one batch. Safe to rerun: loads are idempotent."""
        ctx = TenantContext(tenant_id, "ingest-worker")
        async with self._p.db.tenant_tx(ctx) as conn:
            job = (
                await conn.execute(
                    text(
                        "UPDATE ingestion.batch_job SET status = 'validating' WHERE id = :id "
                        "RETURNING dataset, file_ref"
                    ),
                    {"id": batch_id},
                )
            ).one()
        counts = Counts()
        error: str | None = None
        try:
            data = await self._p.files.get(ctx, job.file_ref)
            reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
            header = set(reader.fieldnames or [])
            allowed, required = _columns(job.dataset)
            if header - allowed or required - header:
                problems = sorted(f"unexpected:{c}" for c in header - allowed) + sorted(
                    f"missing:{c}" for c in required - header
                )
                raise _BadFile("header " + ", ".join(problems))
            await self._set_status(ctx, batch_id, "loading")
            chunk: list[tuple[int | None, dict[str, Any]]] = []
            # Row numbers count from 2 so they match the line in the file (line 1 is the header).
            for number, raw in enumerate(reader, start=2):
                chunk.append((number, _clean(raw)))
                if len(chunk) >= CHUNK_ROWS:
                    counts.add(await self._load(ctx, job.dataset, chunk, batch_id))
                    chunk = []
            if chunk:
                counts.add(await self._load(ctx, job.dataset, chunk, batch_id))
        except _BadFile as exc:
            error = str(exc)
        except UnicodeDecodeError:
            error = "file is not UTF-8"
        except csv.Error:
            error = "file is not valid CSV"
        except Exception as exc:
            # The message may quote row content, so only the error type is stored.
            log.exception("batch_failed", batch_id=str(batch_id))
            error = f"internal error: {type(exc).__name__}"
        status = (
            "failed" if error else "completed_with_errors" if counts.quarantined else "completed"
        )
        async with self._p.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "UPDATE ingestion.batch_job SET status = :s, records_total = :t, "
                        "records_loaded = :l, records_quarantined = :q, error = :e, "
                        f"completed_at = now() WHERE id = :id RETURNING {_JOB_COLUMNS}"
                    ),
                    {
                        "s": status,
                        "t": counts.total,
                        "l": counts.loaded,
                        "q": counts.quarantined,
                        "e": error,
                        "id": batch_id,
                    },
                )
            ).one()
            await conn.execute(
                text("UPDATE ingestion.batch_queue SET status = 'done' WHERE batch_id = :b"),
                {"b": batch_id},
            )
            await record_audit(
                conn,
                ctx,
                action="ingestion.batch.complete",
                resource=f"batch:{batch_id}",
                details={
                    "status": status,
                    "total": counts.total,
                    "loaded": counts.loaded,
                    "quarantined": counts.quarantined,
                    "duplicates": counts.duplicates,
                },
            )
        return BatchJob.from_row(row)

    async def _load(
        self,
        ctx: TenantContext,
        dataset: Dataset,
        chunk: list[tuple[int | None, dict[str, Any]]],
        batch_id: UUID,
    ) -> Counts:
        return await self._ingestion.ingest_rows(
            ctx, dataset, chunk, source="batch", batch_id=batch_id, flat_transactions=True
        )

    async def _set_status(self, ctx: TenantContext, batch_id: UUID, status: str) -> None:
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text("UPDATE ingestion.batch_job SET status = :s WHERE id = :id"),
                {"s": status, "id": batch_id},
            )


class _BadFile(Exception):
    pass


def _clean(raw: dict[str | None, Any]) -> dict[str, Any]:
    """Drop the overflow key csv adds for rows with too many cells; keep it as an error marker."""
    out = {str(k): v for k, v in raw.items() if k is not None}
    if None in raw:
        out["__extra_cells__"] = "present"
    return out
