"""Append-only, hash-chained audit log.

Each record's hash covers its own content and the previous record's hash for the same
tenant. Removing or changing a record breaks every hash after it.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sentinel_core.context import TenantContext
from sentinel_core.ids import uuid7

GENESIS_HASH = "0" * 64


def canonical_hash(
    *,
    tenant_id: UUID,
    sequence: int,
    event_id: UUID,
    occurred_at: datetime,
    actor_id: str,
    actor_type: str,
    action: str,
    resource: str,
    purpose: str | None,
    details: dict[str, Any],
    previous_hash: str,
) -> str:
    body = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "sequence": sequence,
            "id": str(event_id),
            "occurred_at": occurred_at.astimezone(UTC).isoformat(timespec="microseconds"),
            "actor_id": actor_id,
            "actor_type": actor_type,
            "action": action,
            "resource": resource,
            "purpose": purpose,
            "details": details,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def record_audit(
    conn: AsyncConnection,
    ctx: TenantContext,
    *,
    action: str,
    resource: str,
    details: dict[str, Any] | None = None,
) -> int:
    """Append an audit record in the caller's transaction. Returns its sequence number.

    `details` must hold tokens and identifiers only, never clear personal data.
    """
    details = details or {}
    head = (
        await conn.execute(
            text(
                "SELECT sequence, hash FROM core.audit_chain_head WHERE tenant_id = :t FOR UPDATE"
            ),
            {"t": ctx.tenant_id},
        )
    ).first()
    if head is None:
        raise RuntimeError(f"tenant {ctx.tenant_id} has no audit chain")
    sequence = head.sequence + 1
    event_id = uuid7()
    occurred_at = datetime.now(UTC)
    record_hash = canonical_hash(
        tenant_id=ctx.tenant_id,
        sequence=sequence,
        event_id=event_id,
        occurred_at=occurred_at,
        actor_id=ctx.actor_id,
        actor_type=ctx.actor_type,
        action=action,
        resource=resource,
        purpose=ctx.purpose,
        details=details,
        previous_hash=head.hash,
    )
    await conn.execute(
        text(
            "INSERT INTO core.audit_event (tenant_id, sequence, id, occurred_at, actor_id, actor_type, "
            "action, resource, purpose, details, previous_hash, hash) VALUES (:t, :s, :id, :at, :actor, "
            ":atype, :action, :resource, :purpose, CAST(:details AS jsonb), :prev, :hash)"
        ),
        {
            "t": ctx.tenant_id,
            "s": sequence,
            "id": event_id,
            "at": occurred_at,
            "actor": ctx.actor_id,
            "atype": ctx.actor_type,
            "action": action,
            "resource": resource,
            "purpose": ctx.purpose,
            "details": json.dumps(details),
            "prev": head.hash,
            "hash": record_hash,
        },
    )
    await conn.execute(
        text("UPDATE core.audit_chain_head SET sequence = :s, hash = :h WHERE tenant_id = :t"),
        {"s": sequence, "h": record_hash, "t": ctx.tenant_id},
    )
    return int(sequence)


@dataclass(frozen=True)
class Verification:
    verified: bool
    records_checked: int
    first_break_sequence: int | None
    last_anchor_at: datetime | None


def anchor_signature(audit_key: bytes, tenant_id: UUID, sequence: int, record_hash: str) -> str:
    message = f"{tenant_id}|{sequence}|{record_hash}".encode()
    return hmac.new(audit_key, message, hashlib.sha256).hexdigest()


async def verify_chain(
    conn: AsyncConnection, ctx: TenantContext, audit_key: bytes | None = None
) -> Verification:
    """Recompute the tenant's chain and report the first break.

    Checks each record's hash, its link to the previous record, gap-free sequence numbers, and
    that the chain head and every anchor match the records. Anchor signatures are checked when
    the tenant's audit key is supplied.
    """
    rows = await conn.stream(
        text(
            "SELECT sequence, id, occurred_at, actor_id, actor_type, action, resource, purpose, "
            "details, previous_hash, hash FROM core.audit_event ORDER BY sequence"
        )
    )
    previous = GENESIS_HASH
    expected = 1
    checked = 0
    hashes_at: dict[int, str] = {}
    broken: int | None = None
    async for row in rows:
        computed = canonical_hash(
            tenant_id=ctx.tenant_id,
            sequence=row.sequence,
            event_id=row.id,
            occurred_at=row.occurred_at,
            actor_id=row.actor_id,
            actor_type=row.actor_type,
            action=row.action,
            resource=row.resource,
            purpose=row.purpose,
            details=row.details,
            previous_hash=row.previous_hash,
        )
        if row.sequence != expected or row.previous_hash != previous or computed != row.hash:
            broken = min(row.sequence, expected)
            break
        hashes_at[row.sequence] = row.hash
        previous = row.hash
        expected += 1
        checked += 1

    anchors = (
        await conn.execute(
            text(
                "SELECT sequence, hash, signature, anchored_at FROM core.audit_anchor ORDER BY sequence"
            )
        )
    ).all()
    last_anchor_at = anchors[-1].anchored_at if anchors else None
    if broken is None:
        head = (
            await conn.execute(text("SELECT sequence, hash FROM core.audit_chain_head"))
        ).first()
        if head is None or head.sequence != checked or head.hash != previous:
            broken = checked + 1
    if broken is None:
        for anchor in anchors:
            bad_hash = hashes_at.get(anchor.sequence) != anchor.hash
            bad_sig = audit_key is not None and not hmac.compare_digest(
                anchor.signature,
                anchor_signature(audit_key, ctx.tenant_id, anchor.sequence, anchor.hash),
            )
            if bad_hash or bad_sig:
                broken = anchor.sequence
                break
    return Verification(broken is None, checked, broken, last_anchor_at)


async def anchor_chain(conn: AsyncConnection, ctx: TenantContext, audit_key: bytes) -> int | None:
    """Sign the current chain head and store it as an anchor. Returns the anchored sequence."""
    head = (await conn.execute(text("SELECT sequence, hash FROM core.audit_chain_head"))).first()
    if head is None or head.sequence == 0:
        return None
    await conn.execute(
        text(
            "INSERT INTO core.audit_anchor (tenant_id, sequence, hash, signature) "
            "VALUES (:t, :s, :h, :sig) ON CONFLICT DO NOTHING"
        ),
        {
            "t": ctx.tenant_id,
            "s": head.sequence,
            "h": head.hash,
            "sig": anchor_signature(audit_key, ctx.tenant_id, head.sequence, head.hash),
        },
    )
    sequence: int = head.sequence
    return sequence
