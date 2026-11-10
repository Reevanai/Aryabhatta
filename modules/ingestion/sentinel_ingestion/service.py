"""Validates, tokenises and stores incoming records; quarantines what fails."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sentinel_core.context import TenantContext
from sentinel_core.crypto import decrypt, encrypt
from sentinel_core.events import build_event
from sentinel_core.ids import uuid7
from sentinel_core.outbox import add_many_to_outbox
from sentinel_core.runtime import Platform
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sentinel_ingestion.domain.models import (
    AccountRow,
    Dataset,
    DeviceRow,
    LoanRow,
    PartyRow,
    RowError,
    TransactionInput,
    safe_errors,
    transaction_from_csv,
)

Source = Literal["batch", "http", "stream"]
TRANSACTION_TOPIC = "sentinel.ingestion.transaction.v1"
PRODUCER = "ingestion"


@dataclass
class Counts:
    total: int = 0
    loaded: int = 0
    quarantined: int = 0
    duplicates: int = 0

    def add(self, other: "Counts") -> None:
        self.total += other.total
        self.loaded += other.loaded
        self.quarantined += other.quarantined
        self.duplicates += other.duplicates


@dataclass
class _Rejected:
    row_number: int | None
    raw: dict[str, Any]
    errors: list[RowError]


@dataclass
class _Chunk:
    rejected: list[_Rejected] = field(default_factory=list)
    counts: Counts = field(default_factory=Counts)

    def reject(self, row_number: int | None, raw: dict[str, Any], *errors: RowError) -> None:
        self.rejected.append(_Rejected(row_number, raw, list(errors)))


def _ref_error(field_name: str, message: str) -> RowError:
    return RowError(field=field_name, rule="reference", message=message)


class IngestionService:
    def __init__(self, platform: Platform) -> None:
        self._p = platform
        self._partitions: set[tuple[int, int]] = set()

    # Public entry points -------------------------------------------------------

    async def ingest_rows(
        self,
        ctx: TenantContext,
        dataset: Dataset,
        rows: Sequence[tuple[int | None, dict[str, Any]]],
        *,
        source: Source,
        batch_id: UUID | None = None,
        flat_transactions: bool = False,
    ) -> Counts:
        """Process one chunk of raw rows in a single transaction."""
        if not ctx.institution_wide:
            raise PermissionError("ingestion requires an institution-wide context")
        chunk = _Chunk()
        chunk.counts.total = len(rows)
        if dataset == "parties":
            await self._parties(ctx, rows, chunk)
        elif dataset == "accounts":
            await self._accounts(ctx, rows, chunk)
        elif dataset == "loans":
            await self._loans(ctx, rows, chunk)
        elif dataset == "devices":
            await self._devices(ctx, rows, chunk)
        else:
            await self._transactions(ctx, rows, chunk, flat_transactions)
        if chunk.rejected:
            await self._quarantine(ctx, dataset, source, batch_id, chunk.rejected)
        chunk.counts.quarantined = len(chunk.rejected)
        return chunk.counts

    async def read_quarantined(self, ctx: TenantContext, record_id: UUID) -> dict[str, Any]:
        """Decrypt a quarantined row. Callers must hold a purpose that allows clear data."""
        async with self._p.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT raw_ciphertext, key_version FROM ingestion.quarantine_record "
                        "WHERE id = :id"
                    ),
                    {"id": record_id},
                )
            ).one()
        key = await self._p.keys.get(ctx, "file", row.key_version)
        aad = f"{ctx.tenant_id}|quarantine|{record_id}".encode()
        loaded: dict[str, Any] = json.loads(decrypt(key.key, bytes(row.raw_ciphertext), aad))
        return loaded

    # Parties ----------------------------------------------------------------------

    async def _parties(
        self, ctx: TenantContext, rows: Sequence[tuple[int | None, dict[str, Any]]], chunk: _Chunk
    ) -> None:
        valid: list[PartyRow] = []
        for number, raw in rows:
            try:
                valid.append(PartyRow.model_validate(raw))
            except ValidationError as exc:
                chunk.reject(number, raw, *safe_errors(exc))
        if not valid:
            return
        items: list[tuple[str, str]] = []
        slots: list[dict[str, int]] = []
        for row in valid:
            slot: dict[str, int] = {}
            for column, field_type, value in (
                ("party_token", "cust", row.customer_id),
                ("name_token", "name", row.full_name),
                ("pan_token", "pan", row.pan),
                ("phone_token", "phone", row.phone),
                ("email_token", "email", row.email),
                ("address_token", "addr", row.address),
                ("dob_token", "dob", row.date_of_birth.isoformat() if row.date_of_birth else None),
            ):
                if value is not None:
                    slot[column] = len(items)
                    items.append((field_type, value))
            slots.append(slot)
        tokens = await self._p.tokeniser.tokenise_many(ctx, items)
        params = []
        for row, slot in zip(valid, slots, strict=True):
            record: dict[str, Any] = {
                "id": uuid7(),
                "t": ctx.tenant_id,
                "party_type": row.party_type,
                "risk": row.kyc_risk_category,
                "onboarded": row.onboarded_on,
            }
            for column in (
                "party_token",
                "name_token",
                "pan_token",
                "phone_token",
                "email_token",
                "address_token",
                "dob_token",
            ):
                record[column] = tokens[slot[column]] if column in slot else None
            params.append(record)
        # Last row wins when a file repeats a customer.
        unique = list({p["party_token"]: p for p in params}.values())
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO ingestion.party (id, tenant_id, party_token, party_type, name_token, "
                    "pan_token, phone_token, email_token, address_token, dob_token, kyc_risk_category, "
                    "onboarded_on) VALUES (:id, :t, :party_token, :party_type, :name_token, :pan_token, "
                    ":phone_token, :email_token, :address_token, :dob_token, :risk, :onboarded) "
                    "ON CONFLICT (tenant_id, party_token) DO UPDATE SET party_type = EXCLUDED.party_type, "
                    "name_token = EXCLUDED.name_token, pan_token = EXCLUDED.pan_token, "
                    "phone_token = EXCLUDED.phone_token, email_token = EXCLUDED.email_token, "
                    "address_token = EXCLUDED.address_token, dob_token = EXCLUDED.dob_token, "
                    "kyc_risk_category = EXCLUDED.kyc_risk_category, "
                    "onboarded_on = EXCLUDED.onboarded_on, updated_at = now()"
                ),
                unique,
            )
        chunk.counts.loaded += len(valid)

    # Accounts ---------------------------------------------------------------------

    async def _accounts(
        self, ctx: TenantContext, rows: Sequence[tuple[int | None, dict[str, Any]]], chunk: _Chunk
    ) -> None:
        valid: list[tuple[int | None, dict[str, Any], AccountRow]] = []
        for number, raw in rows:
            try:
                valid.append((number, raw, AccountRow.model_validate(raw)))
            except ValidationError as exc:
                chunk.reject(number, raw, *safe_errors(exc))
        if not valid:
            return
        items = [
            pair
            for _, _, r in valid
            for pair in (("acct", r.account_number), ("cust", r.customer_id))
        ]
        tokens = await self._p.tokeniser.tokenise_many(ctx, items)
        async with self._p.db.tenant_tx(ctx) as conn:
            branches = await _branches(conn)
            parties = await _party_ids(conn, tokens[1::2])
            params = []
            for index, (number, raw, row) in enumerate(valid):
                branch_id = branches.get(row.branch_code)
                party_id = parties.get(tokens[index * 2 + 1])
                errors = []
                if branch_id is None:
                    errors.append(_ref_error("branch_code", "unknown branch"))
                if party_id is None:
                    errors.append(_ref_error("customer_id", "unknown customer"))
                if errors:
                    chunk.reject(number, raw, *errors)
                    continue
                params.append(
                    {
                        "id": uuid7(),
                        "t": ctx.tenant_id,
                        "b": branch_id,
                        "tok": tokens[index * 2],
                        "p": party_id,
                        "pt": row.product_type,
                        "s": row.status,
                        "o": row.opened_on,
                        "c": row.currency,
                    }
                )
            unique = list({p["tok"]: p for p in params}.values())
            if unique:
                await conn.execute(
                    text(
                        "INSERT INTO ingestion.account (id, tenant_id, branch_id, account_token, party_id, "
                        "product_type, status, opened_on, currency) VALUES (:id, :t, :b, :tok, :p, :pt, "
                        ":s, :o, :c) ON CONFLICT (tenant_id, account_token) DO UPDATE SET "
                        "branch_id = EXCLUDED.branch_id, party_id = EXCLUDED.party_id, "
                        "product_type = EXCLUDED.product_type, status = EXCLUDED.status, "
                        "opened_on = EXCLUDED.opened_on, currency = EXCLUDED.currency, updated_at = now()"
                    ),
                    unique,
                )
            chunk.counts.loaded += len(params)

    # Loans ------------------------------------------------------------------------

    async def _loans(
        self, ctx: TenantContext, rows: Sequence[tuple[int | None, dict[str, Any]]], chunk: _Chunk
    ) -> None:
        valid: list[tuple[int | None, dict[str, Any], LoanRow]] = []
        for number, raw in rows:
            try:
                valid.append((number, raw, LoanRow.model_validate(raw)))
            except ValidationError as exc:
                chunk.reject(number, raw, *safe_errors(exc))
        if not valid:
            return
        tokens = await self._p.tokeniser.tokenise_many(
            ctx,
            [
                pair
                for _, _, r in valid
                for pair in (("acct", r.account_number), ("cust", r.customer_id))
            ],
        )
        async with self._p.db.tenant_tx(ctx) as conn:
            accounts = await _accounts_by_token(conn, tokens[0::2])
            parties = await _party_ids(conn, tokens[1::2])
            params: dict[str, dict[str, Any]] = {}
            for index, (number, raw, row) in enumerate(valid):
                account = accounts.get(tokens[index * 2])
                party_id = parties.get(tokens[index * 2 + 1])
                errors = []
                if party_id is None:
                    errors.append(_ref_error("customer_id", "unknown customer"))
                if account is None:
                    errors.append(_ref_error("account_number", "unknown account"))
                elif party_id is not None and account.party_token != tokens[index * 2 + 1]:
                    errors.append(
                        _ref_error("account_number", "account belongs to another customer")
                    )
                if errors or account is None:
                    chunk.reject(number, raw, *errors)
                    continue
                params[row.loan_id] = {
                    "id": uuid7(),
                    "t": ctx.tenant_id,
                    "b": account.branch_id,
                    "ref": row.loan_id,
                    "p": party_id,
                    "a": account.id,
                    "product": row.product,
                    "principal": row.principal,
                    "rate": row.annual_interest_rate,
                    "tenure": row.tenure_months,
                    "disbursed": row.disbursed_on,
                    "emi": row.emi_amount,
                    "status": row.status,
                    "dpd": row.days_past_due,
                    "cur": row.currency,
                }
                chunk.counts.loaded += 1
            if params:
                await conn.execute(
                    text(
                        "INSERT INTO ingestion.loan (id, tenant_id, branch_id, loan_ref, party_id, "
                        "account_id, product, principal, annual_interest_rate, tenure_months, "
                        "disbursed_on, emi_amount, status, days_past_due, currency) VALUES (:id, :t, :b, "
                        ":ref, :p, :a, :product, :principal, :rate, :tenure, :disbursed, :emi, :status, "
                        ":dpd, :cur) ON CONFLICT (tenant_id, loan_ref) DO UPDATE SET "
                        "status = EXCLUDED.status, days_past_due = EXCLUDED.days_past_due, "
                        "emi_amount = EXCLUDED.emi_amount, principal = EXCLUDED.principal, "
                        "annual_interest_rate = EXCLUDED.annual_interest_rate, "
                        "tenure_months = EXCLUDED.tenure_months, updated_at = now()"
                    ),
                    list(params.values()),
                )

    # Devices ----------------------------------------------------------------------

    async def _devices(
        self, ctx: TenantContext, rows: Sequence[tuple[int | None, dict[str, Any]]], chunk: _Chunk
    ) -> None:
        valid: list[tuple[int | None, dict[str, Any], DeviceRow]] = []
        for number, raw in rows:
            try:
                valid.append((number, raw, DeviceRow.model_validate(raw)))
            except ValidationError as exc:
                chunk.reject(number, raw, *safe_errors(exc))
        if not valid:
            return
        tokens = await self._p.tokeniser.tokenise_many(
            ctx,
            [pair for _, _, r in valid for pair in (("dev", r.device_id), ("cust", r.customer_id))],
        )
        async with self._p.db.tenant_tx(ctx) as conn:
            parties = await _party_ids(conn, tokens[1::2])
            params: dict[tuple[str, Any], dict[str, Any]] = {}
            for index, (number, raw, row) in enumerate(valid):
                party_id = parties.get(tokens[index * 2 + 1])
                if party_id is None:
                    chunk.reject(number, raw, _ref_error("customer_id", "unknown customer"))
                    continue
                params[(tokens[index * 2], party_id)] = {
                    "id": uuid7(),
                    "t": ctx.tenant_id,
                    "d": tokens[index * 2],
                    "p": party_id,
                    "platform": row.platform,
                    "os": row.os_version,
                    "rooted": row.rooted,
                    "seen": row.first_seen,
                }
                chunk.counts.loaded += 1
            if params:
                await conn.execute(
                    text(
                        "INSERT INTO ingestion.device_link (id, tenant_id, device_token, party_id, "
                        "platform, os_version, rooted, first_seen) VALUES (:id, :t, :d, :p, :platform, "
                        ":os, :rooted, :seen) ON CONFLICT (tenant_id, device_token, party_id) DO UPDATE "
                        "SET platform = EXCLUDED.platform, os_version = EXCLUDED.os_version, "
                        "rooted = EXCLUDED.rooted, "
                        "first_seen = LEAST(ingestion.device_link.first_seen, EXCLUDED.first_seen), "
                        "updated_at = now()"
                    ),
                    list(params.values()),
                )

    # Transactions -----------------------------------------------------------------

    async def _transactions(
        self,
        ctx: TenantContext,
        rows: Sequence[tuple[int | None, dict[str, Any]]],
        chunk: _Chunk,
        flat: bool,
    ) -> None:
        valid: list[tuple[int | None, dict[str, Any], TransactionInput]] = []
        for number, raw in rows:
            try:
                parsed = transaction_from_csv(raw) if flat else TransactionInput.model_validate(raw)
                valid.append((number, raw, parsed))
            except ValidationError as exc:
                chunk.reject(number, raw, *safe_errors(exc))
        if not valid:
            return
        items: list[tuple[str, str]] = []
        slots: list[dict[str, int]] = []
        for _, _, txn in valid:
            slot: dict[str, int] = {}
            for name, field_type, value in (
                ("account", "acct", txn.account_number),
                ("cp_account", "acct", txn.counterparty_account_number),
                ("cp_vpa", "vpa", txn.counterparty_vpa),
                ("device", "dev", txn.device_id),
                ("ip", "ip", txn.ip_address),
            ):
                if value is not None:
                    slot[name] = len(items)
                    items.append((field_type, value))
            slots.append(slot)
        tokens = await self._p.tokeniser.tokenise_many(ctx, items)

        async with self._p.db.tenant_tx(ctx) as conn:
            branches = await _branches(conn)
            accounts = await _accounts_by_token(conn, [tokens[s["account"]] for s in slots])
            candidates: dict[str, dict[str, Any]] = {}
            for (number, raw, txn), slot in zip(valid, slots, strict=True):
                account_token = tokens[slot["account"]]
                account = accounts.get(account_token)
                if account is None:
                    chunk.reject(number, raw, _ref_error("account_number", "unknown account"))
                    continue
                branch_id = account.branch_id
                if txn.branch_code is not None:
                    known = branches.get(txn.branch_code)
                    if known is None:
                        chunk.reject(number, raw, _ref_error("branch_code", "unknown branch"))
                        continue
                    branch_id = known
                if txn.source_reference in candidates:
                    chunk.counts.duplicates += 1
                    continue
                await self._ensure_partition(conn, txn.booked_at.date())
                optional = {
                    name: tokens[slot[name]] if name in slot else None
                    for name in ("cp_account", "cp_vpa", "device", "ip")
                }
                candidates[txn.source_reference] = {
                    "id": uuid7(),
                    "t": ctx.tenant_id,
                    "b": branch_id,
                    "ref": txn.source_reference,
                    "ch": txn.channel,
                    "dir": txn.direction,
                    "amt": txn.amount.amount,
                    "cur": txn.amount.currency,
                    "at": txn.booked_at,
                    "acc": account.id,
                    "acct": account_token,
                    "party": account.party_token,
                    "cpa": optional["cp_account"],
                    "cpv": optional["cp_vpa"],
                    "cpb": txn.counterparty_bank_code,
                    "dev": optional["device"],
                    "ip": optional["ip"],
                    "mcc": txn.merchant_category,
                    "st": txn.status,
                    "attrs": json.dumps(txn.attributes),
                    "attributes": txn.attributes,
                }
            if not candidates:
                return
            # Claim the references in one statement; only unclaimed ones are new.
            claimed = {
                row.source_reference
                for row in await conn.execute(
                    text(
                        "INSERT INTO ingestion.transaction_ref (tenant_id, source_reference, "
                        "transaction_id) SELECT :t, u.ref, u.id FROM unnest(CAST(:refs AS text[]), "
                        "CAST(:ids AS uuid[])) AS u(ref, id) ON CONFLICT DO NOTHING "
                        "RETURNING source_reference"
                    ),
                    {
                        "t": ctx.tenant_id,
                        "refs": list(candidates),
                        "ids": [c["id"] for c in candidates.values()],
                    },
                )
            }
            fresh = [c for ref, c in candidates.items() if ref in claimed]
            chunk.counts.duplicates += len(candidates) - len(fresh)
            if not fresh:
                return
            await conn.execute(
                text(
                    "INSERT INTO ingestion.transaction (id, tenant_id, branch_id, source_reference, "
                    "channel, direction, amount, currency, booked_at, account_id, account_token, "
                    "party_token, counterparty_account_token, counterparty_vpa_token, "
                    "counterparty_bank_code, device_token, ip_token, merchant_category, status, "
                    "attributes) VALUES (:id, :t, :b, :ref, :ch, :dir, :amt, :cur, :at, :acc, "
                    ":acct, :party, :cpa, :cpv, :cpb, :dev, :ip, :mcc, :st, CAST(:attrs AS jsonb))"
                ),
                [{k: v for k, v in c.items() if k != "attributes"} for c in fresh],
            )
            events = [
                (
                    f"{ctx.tenant_id}:{c['acct']}",
                    build_event(
                        ctx,
                        event_type="ingestion.transaction.received",
                        producer=PRODUCER,
                        branch_id=c["b"],
                        payload={
                            "transaction_id": str(c["id"]),
                            "source_reference": c["ref"],
                            "channel": c["ch"],
                            "direction": c["dir"],
                            "amount": {"amount": f"{c['amt']:.2f}", "currency": c["cur"]},
                            "booked_at": c["at"].isoformat(),
                            "account_token": c["acct"],
                            "party_token": c["party"],
                            "counterparty_account_token": c["cpa"],
                            "counterparty_vpa_token": c["cpv"],
                            "counterparty_bank_code": c["cpb"],
                            "device_token": c["dev"],
                            "ip_token": c["ip"],
                            "merchant_category": c["mcc"],
                            "status": c["st"],
                            "attributes": c["attributes"],
                        },
                    ),
                )
                for c in fresh
            ]
            await add_many_to_outbox(conn, topic=TRANSACTION_TOPIC, events=events)
            chunk.counts.loaded += len(fresh)

    async def _ensure_partition(self, conn: AsyncConnection, day: date) -> None:
        month = (day.year, day.month)
        if month not in self._partitions:
            await conn.execute(
                text("SELECT ingestion.ensure_transaction_partition(:d)"), {"d": day}
            )
            self._partitions.add(month)

    # Quarantine ---------------------------------------------------------------------

    async def _quarantine(
        self,
        ctx: TenantContext,
        dataset: str,
        source: Source,
        batch_id: UUID | None,
        rejected: list[_Rejected],
    ) -> None:
        key = await self._p.keys.get(ctx, "file")
        params = []
        for item in rejected:
            record_id = uuid7()
            aad = f"{ctx.tenant_id}|quarantine|{record_id}".encode()
            params.append(
                {
                    "id": record_id,
                    "t": ctx.tenant_id,
                    "b": batch_id,
                    "src": source,
                    "ds": dataset,
                    "n": item.row_number,
                    "raw": encrypt(key.key, json.dumps(item.raw, default=str).encode(), aad),
                    "kv": key.version,
                    "errors": json.dumps([e.model_dump() for e in item.errors]),
                }
            )
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO ingestion.quarantine_record (id, tenant_id, batch_id, source, dataset, "
                    "row_number, raw_ciphertext, key_version, errors) VALUES (:id, :t, :b, :src, :ds, :n, "
                    ":raw, :kv, CAST(:errors AS jsonb))"
                ),
                params,
            )


@dataclass(frozen=True)
class _Account:
    id: UUID
    branch_id: UUID
    party_token: str


async def _branches(conn: AsyncConnection) -> dict[str, UUID]:
    rows = await conn.execute(text("SELECT code, id FROM core.branch WHERE status = 'active'"))
    return {row.code: row.id for row in rows}


async def _party_ids(conn: AsyncConnection, tokens: Sequence[str]) -> dict[str, UUID]:
    rows = await conn.execute(
        text("SELECT party_token, id FROM ingestion.party WHERE party_token = ANY(:tokens)"),
        {"tokens": list(set(tokens))},
    )
    return {row.party_token: row.id for row in rows}


async def _accounts_by_token(conn: AsyncConnection, tokens: Sequence[str]) -> dict[str, _Account]:
    rows = await conn.execute(
        text(
            "SELECT a.account_token, a.id, a.branch_id, p.party_token FROM ingestion.account a "
            "JOIN ingestion.party p ON p.id = a.party_id WHERE a.account_token = ANY(:tokens)"
        ),
        {"tokens": list(set(tokens))},
    )
    return {row.account_token: _Account(row.id, row.branch_id, row.party_token) for row in rows}
