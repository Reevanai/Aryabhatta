"""HTTP endpoints for ingestion."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sentinel_core.audit import record_audit
from sentinel_core.errors import Forbidden, TooLarge
from sentinel_core.pagination import decode_cursor, encode_cursor
from sentinel_core.web import PlatformDep, PrincipalDep
from sqlalchemy import text

from sentinel_ingestion.batches import BatchJob, BatchService
from sentinel_ingestion.service import IngestionService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
PURPOSE = "service_operations"


def get_ingestion(request: Request) -> IngestionService:
    service: IngestionService = request.app.state.ingestion
    return service


def get_batches(request: Request) -> BatchService:
    service: BatchService = request.app.state.batches
    return service


IngestionDep = Annotated[IngestionService, Depends(get_ingestion)]
BatchesDep = Annotated[BatchService, Depends(get_batches)]


class BatchJobOut(BaseModel):
    id: UUID
    dataset: str
    status: str
    records_total: int
    records_loaded: int
    records_quarantined: int
    file_sha256: str
    created_at: Any
    completed_at: Any | None
    error: str | None

    @classmethod
    def of(cls, job: BatchJob) -> "BatchJobOut":
        return cls.model_validate(job, from_attributes=True)


def _require_institution_wide(principal: PrincipalDep) -> None:
    if principal.branch_ids:
        raise Forbidden("ingestion requires institution-wide access")


@router.post("/batches", status_code=status.HTTP_202_ACCEPTED, response_model=BatchJobOut)
async def create_batch(
    principal: PrincipalDep,
    platform: PlatformDep,
    batches: BatchesDep,
    dataset: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> BatchJobOut:
    principal.require("ingestion:write")
    _require_institution_wide(principal)
    limit = platform.settings.max_batch_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise TooLarge(f"file exceeds {limit} bytes")
    job, _created = await batches.create(principal.context(PURPOSE), dataset, data)
    return BatchJobOut.of(job)


@router.get("/batches/{batch_id}", response_model=BatchJobOut)
async def get_batch(principal: PrincipalDep, batches: BatchesDep, batch_id: UUID) -> BatchJobOut:
    principal.require("ingestion:read")
    return BatchJobOut.of(await batches.get(principal.context(PURPOSE), batch_id))


class QuarantineOut(BaseModel):
    id: UUID
    batch_id: UUID | None
    source: str
    dataset: str
    row_number: int | None
    errors: list[dict[str, str]]
    status: str


class QuarantinePage(BaseModel):
    items: list[QuarantineOut]
    next_cursor: str | None


@router.get("/quarantine", response_model=QuarantinePage)
async def list_quarantine(
    principal: PrincipalDep,
    platform: PlatformDep,
    batch_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> QuarantinePage:
    """Lists rejected records and why. The rejected content itself is not returned."""
    principal.require("ingestion:read")
    after = decode_cursor(cursor, 2)
    async with platform.db.tenant_tx(principal.context(PURPOSE)) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, batch_id, source, dataset, row_number, errors, status, created_at "
                    "FROM ingestion.quarantine_record "
                    "WHERE (CAST(:b AS uuid) IS NULL OR batch_id = :b) "
                    "AND (CAST(:at AS timestamptz) IS NULL OR (created_at, id) > (:at, :id)) "
                    "ORDER BY created_at, id LIMIT :n"
                ),
                {
                    "b": batch_id,
                    "at": after[0] if after else None,
                    "id": UUID(after[1]) if after else None,
                    "n": limit + 1,
                },
            )
        ).all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor([page[-1].created_at.isoformat(), str(page[-1].id)])
        if len(rows) > limit
        else None
    )
    return QuarantinePage(
        items=[QuarantineOut.model_validate(r, from_attributes=True) for r in page],
        next_cursor=next_cursor,
    )


class EventsIn(BaseModel):
    # Rows are validated one by one so that a bad row is quarantined, not the whole request.
    events: list[dict[str, Any]] = Field(max_length=500)


class EventsOut(BaseModel):
    accepted: int
    quarantined: int
    duplicates: int


@router.post("/events", status_code=status.HTTP_202_ACCEPTED, response_model=EventsOut)
async def post_events(
    principal: PrincipalDep, platform: PlatformDep, ingestion: IngestionDep, body: EventsIn
) -> EventsOut:
    principal.require("ingestion:write")
    _require_institution_wide(principal)
    ctx = principal.context(PURPOSE)
    counts = await ingestion.ingest_rows(
        ctx, "transactions", [(None, e) for e in body.events], source="http"
    )
    async with platform.db.tenant_tx(ctx) as conn:
        await record_audit(
            conn,
            ctx,
            action="ingestion.events.submit",
            resource="transactions",
            details={
                "total": counts.total,
                "loaded": counts.loaded,
                "quarantined": counts.quarantined,
                "duplicates": counts.duplicates,
            },
        )
    return EventsOut(
        accepted=counts.loaded, quarantined=counts.quarantined, duplicates=counts.duplicates
    )
