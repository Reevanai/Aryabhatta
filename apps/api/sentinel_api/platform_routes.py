"""Platform endpoints: tenant, branches, audit, PII reveal."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field
from sentinel_core.audit import record_audit, verify_chain
from sentinel_core.context import PURPOSES
from sentinel_core.errors import Conflict, Invalid, NotFound
from sentinel_core.ids import uuid7
from sentinel_core.pagination import decode_cursor, encode_cursor
from sentinel_core.web import PlatformDep, PrincipalDep
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

router = APIRouter(tags=["platform"])


class TenantOut(BaseModel):
    id: UUID
    name: str
    institution_type: str
    status: str


@router.get("/tenant", response_model=TenantOut)
async def get_tenant(principal: PrincipalDep, platform: PlatformDep) -> TenantOut:
    principal.require("tenant:read")
    async with platform.db.tenant_tx(principal.context()) as conn:
        row = (
            await conn.execute(text("SELECT id, name, institution_type, status FROM core.tenant"))
        ).first()
    if row is None:
        raise NotFound("tenant not found")
    return TenantOut.model_validate(row, from_attributes=True)


class BranchCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    parent_branch_id: UUID | None = None


class BranchOut(BranchCreate):
    id: UUID
    status: str


class BranchPage(BaseModel):
    items: list[BranchOut]
    next_cursor: str | None


@router.get("/branches", response_model=BranchPage)
async def list_branches(
    principal: PrincipalDep,
    platform: PlatformDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BranchPage:
    principal.require("branch:read")
    after = decode_cursor(cursor, 1)
    async with platform.db.tenant_tx(principal.context()) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, code, name, parent_branch_id, status FROM core.branch "
                    "WHERE (CAST(:after AS text) IS NULL OR code > :after) ORDER BY code LIMIT :n"
                ),
                {"after": after[0] if after else None, "n": limit + 1},
            )
        ).all()
    page = rows[:limit]
    return BranchPage(
        items=[BranchOut.model_validate(r, from_attributes=True) for r in page],
        next_cursor=encode_cursor([page[-1].code]) if len(rows) > limit else None,
    )


@router.post("/branches", status_code=status.HTTP_201_CREATED, response_model=BranchOut)
async def create_branch(
    principal: PrincipalDep, platform: PlatformDep, body: BranchCreate
) -> BranchOut:
    principal.require("branch:write")
    ctx = principal.context()
    branch_id = uuid7()
    try:
        async with platform.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "INSERT INTO core.branch (id, tenant_id, code, name, parent_branch_id) "
                        "VALUES (:id, :t, :c, :n, :p) "
                        "RETURNING id, code, name, parent_branch_id, status"
                    ),
                    {
                        "id": branch_id,
                        "t": ctx.tenant_id,
                        "c": body.code,
                        "n": body.name,
                        "p": body.parent_branch_id,
                    },
                )
            ).one()
            await record_audit(
                conn,
                ctx,
                action="branch.create",
                resource=f"branch:{branch_id}",
                details={"code": body.code},
            )
    except IntegrityError as exc:
        raise Conflict("a branch with this code exists, or the parent branch is unknown") from exc
    return BranchOut.model_validate(row, from_attributes=True)


class AuditEventOut(BaseModel):
    id: UUID
    sequence: int
    occurred_at: datetime
    actor_id: str
    actor_type: str
    action: str
    resource: str
    purpose: str | None
    details: dict[str, Any]
    hash: str
    previous_hash: str


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: str | None


@router.get("/audit/events", response_model=AuditPage)
async def list_audit_events(
    principal: PrincipalDep,
    platform: PlatformDep,
    actor_id: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    principal.require("audit:read")
    after = decode_cursor(cursor, 1)
    async with platform.db.tenant_tx(principal.context()) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, sequence, occurred_at, actor_id, actor_type, action, resource, purpose, "
                    "details, hash, previous_hash FROM core.audit_event WHERE "
                    "(CAST(:actor AS text) IS NULL OR actor_id = :actor) AND "
                    "(CAST(:action AS text) IS NULL OR action = :action) AND "
                    "(CAST(:resource AS text) IS NULL OR resource = :resource) AND "
                    "(CAST(:from AS timestamptz) IS NULL OR occurred_at >= :from) AND "
                    "(CAST(:to AS timestamptz) IS NULL OR occurred_at < :to) AND "
                    "(CAST(:after AS bigint) IS NULL OR sequence > :after) "
                    "ORDER BY sequence LIMIT :n"
                ),
                {
                    "actor": actor_id,
                    "action": action,
                    "resource": resource,
                    "from": from_,
                    "to": to,
                    "after": int(after[0]) if after else None,
                    "n": limit + 1,
                },
            )
        ).all()
    page = rows[:limit]
    return AuditPage(
        items=[AuditEventOut.model_validate(r, from_attributes=True) for r in page],
        next_cursor=encode_cursor([page[-1].sequence]) if len(rows) > limit else None,
    )


class AuditVerificationOut(BaseModel):
    verified: bool
    records_checked: int
    first_break_sequence: int | None
    last_anchor_at: datetime | None


@router.get("/audit/verify", response_model=AuditVerificationOut)
async def verify_audit(principal: PrincipalDep, platform: PlatformDep) -> AuditVerificationOut:
    principal.require("audit:verify")
    ctx = principal.context()
    key = await platform.keys.get(ctx, "audit")
    async with platform.db.tenant_tx(ctx) as conn:
        result = await verify_chain(conn, ctx, key.key)
    return AuditVerificationOut.model_validate(result, from_attributes=True)


class RevealRequest(BaseModel):
    token: str
    purpose: str
    reason: str = Field(min_length=1, max_length=500)
    case_id: UUID | None = None


class RevealResponse(BaseModel):
    value: str
    field_type: str


@router.post("/pii/reveal", response_model=RevealResponse)
async def reveal_pii(
    principal: PrincipalDep, platform: PlatformDep, body: RevealRequest, response: Response
) -> RevealResponse:
    principal.require("pii:reveal")
    if body.purpose not in PURPOSES:
        raise Invalid("unknown purpose")
    reason = body.reason if body.case_id is None else f"case:{body.case_id} {body.reason}"
    revealed = await platform.tokeniser.reveal(principal.context(body.purpose), body.token, reason)
    response.headers["Cache-Control"] = "no-store"
    return RevealResponse(value=revealed.value, field_type=revealed.field_type)
