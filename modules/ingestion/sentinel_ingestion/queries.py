"""Read access to ingested data for other modules. They never query these tables directly."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True)
class AccountInfo:
    account_token: str
    account_id: UUID
    branch_id: UUID
    product: str
    opened_on: Any
    party_type: str
    kyc_risk: str | None


_ACCOUNT_SQL = (
    "SELECT a.account_token, a.id AS account_id, a.branch_id, a.product_type AS product, "
    "a.opened_on, p.party_type, p.kyc_risk_category AS kyc_risk "
    "FROM ingestion.account a JOIN ingestion.party p ON p.id = a.party_id"
)


async def accounts_by_token(conn: AsyncConnection, tokens: Sequence[str]) -> dict[str, AccountInfo]:
    rows = await conn.execute(
        text(_ACCOUNT_SQL + " WHERE a.account_token = ANY(:tokens)"), {"tokens": list(set(tokens))}
    )
    return {r.account_token: AccountInfo(*r) for r in rows}


async def all_accounts(conn: AsyncConnection) -> dict[str, AccountInfo]:
    rows = await conn.execute(text(_ACCOUNT_SQL))
    return {r.account_token: AccountInfo(*r) for r in rows}


async def stream_transactions(
    conn: AsyncConnection, start: datetime, end: datetime
) -> AsyncIterator[Any]:
    """Transactions booked in [start, end), oldest first."""
    result = await conn.stream(
        text(
            "SELECT id, branch_id, source_reference, channel, direction, amount, booked_at, "
            "account_token, counterparty_account_token, counterparty_vpa_token, device_token, "
            "ip_token, status, attributes FROM ingestion.transaction "
            "WHERE booked_at >= :start AND booked_at < :end ORDER BY booked_at, id"
        ),
        {"start": start, "end": end},
    )
    async for row in result:
        yield row


async def amount_percentile(
    conn: AsyncConnection, percentile: float, start: datetime, end: datetime
) -> float | None:
    """A percentile of completed outgoing transfer amounts in [start, end)."""
    value = (
        await conn.execute(
            text(
                "SELECT percentile_cont(:p) WITHIN GROUP (ORDER BY amount) FROM ingestion.transaction "
                "WHERE booked_at >= :start AND booked_at < :end AND direction = 'debit' "
                "AND status = 'completed' AND channel IN ('upi', 'imps', 'neft', 'rtgs')"
            ),
            {"p": percentile, "start": start, "end": end},
        )
    ).scalar_one_or_none()
    return float(value) if value is not None else None
