"""Backtests: replay a rule version over stored history and measure it against labels."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sentinel_core.audit import record_audit
from sentinel_core.context import TenantContext
from sentinel_core.errors import Invalid, NotFound
from sentinel_core.ids import uuid7
from sentinel_core.logging import get_logger
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import all_accounts, stream_transactions
from sqlalchemy import text

from sentinel_txn_fraud.domain.features import HISTORY_DAYS, AccountProfile, TxnView
from sentinel_txn_fraud.domain.scoring import RuleSpec, Thresholds, score
from sentinel_txn_fraud.domain.state import MemoryState
from sentinel_txn_fraud.replay import Metrics
from sentinel_txn_fraud.rules import RuleService

log = get_logger(__name__)
MAX_RANGE = timedelta(days=366)
_COLUMNS = (
    "b.id, v.rule_id, v.version, b.status, b.from_ts, b.to_ts, b.transactions_evaluated, b.hits, "
    "b.true_positives, b.false_positives, b.false_negatives, b.precision_value, b.recall_value, "
    "b.alerts_per_day, b.breakdown, b.error"
)


@dataclass(frozen=True)
class Backtest:
    id: UUID
    rule_id: UUID
    version: int
    status: str
    from_: datetime
    to: datetime
    transactions_evaluated: int | None
    hits: int | None
    true_positives: int | None
    false_positives: int | None
    false_negatives: int | None
    precision: float | None
    recall: float | None
    estimated_alerts_per_day: float | None
    breakdown: dict[str, Any] | None
    error: str | None

    @classmethod
    def of(cls, row: Any) -> "Backtest":
        return cls(*row)


class BacktestService:
    def __init__(self, platform: Platform, rules: RuleService | None = None) -> None:
        self._p = platform
        self._rules = rules or RuleService(platform)

    async def start(
        self, ctx: TenantContext, rule_id: UUID, version: int, start: datetime, end: datetime
    ) -> Backtest:
        if start.tzinfo is None or end.tzinfo is None:
            raise Invalid("from and to must include a time zone")
        if not start < end <= start + MAX_RANGE:
            raise Invalid("the range must be positive and at most 366 days")
        rule = await self._rules.get(ctx, rule_id, version)
        backtest_id = uuid7()
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO txn_fraud.backtest_run (id, tenant_id, rule_version_id, from_ts, to_ts, "
                    "created_by) VALUES (:id, :t, :v, :f, :to, :by)"
                ),
                {
                    "id": backtest_id,
                    "t": ctx.tenant_id,
                    "v": rule.id,
                    "f": start,
                    "to": end,
                    "by": ctx.actor_id,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO txn_fraud.backtest_queue (backtest_id, tenant_id) VALUES (:b, :t)"
                ),
                {"b": backtest_id, "t": ctx.tenant_id},
            )
            await record_audit(
                conn,
                ctx,
                action="rule.backtest.start",
                resource=f"rule:{rule.code}",
                details={"version": version, "backtest_id": str(backtest_id)},
            )
        return await self.get(ctx, backtest_id)

    async def get(self, ctx: TenantContext, backtest_id: UUID) -> Backtest:
        async with self._p.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM txn_fraud.backtest_run b "  # noqa: S608
                        "JOIN txn_fraud.rule_version v ON v.id = b.rule_version_id WHERE b.id = :id"
                    ),
                    {"id": backtest_id},
                )
            ).first()
        if row is None:
            raise NotFound("backtest not found")
        return Backtest.of(row)

    async def claim_next(self) -> tuple[UUID, UUID] | None:
        async with self._p.db.system_tx() as conn:
            row = (
                await conn.execute(
                    text(
                        "UPDATE txn_fraud.backtest_queue SET status = 'claimed', claimed_at = now() "
                        "WHERE backtest_id = (SELECT backtest_id FROM txn_fraud.backtest_queue WHERE "
                        "status = 'queued' OR (status = 'claimed' AND claimed_at < now() - interval "
                        "'30 minutes') ORDER BY enqueued_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                        "RETURNING tenant_id, backtest_id"
                    )
                )
            ).first()
        return (row.tenant_id, row.backtest_id) if row else None

    async def run(self, tenant_id: UUID, backtest_id: UUID) -> Backtest:
        """Replay history through the rule, with the same code that scores live transactions.

        The 30 days before the range warm up the account histories and are not counted.
        Recall is measured against every transaction labelled as fraud in the range; the
        breakdown shows it per label group, which is the fairer view for a targeted rule.
        """
        ctx = TenantContext(tenant_id, "backtest-worker")
        async with self._p.db.tenant_tx(ctx) as conn:
            job = (
                await conn.execute(
                    text(
                        "UPDATE txn_fraud.backtest_run b SET status = 'running' FROM txn_fraud.rule_version v, "
                        "txn_fraud.rule r WHERE b.id = :id AND v.id = b.rule_version_id AND r.id = v.rule_id "
                        "RETURNING b.from_ts, b.to_ts, r.code, v.name, v.expression, v.severity, v.channels"
                    ),
                    {"id": backtest_id},
                )
            ).one()
        error: str | None = None
        overall = Metrics()
        groups: dict[str, Metrics] = {}
        try:
            rule = RuleSpec(
                job.code, job.name, job.expression, job.severity, 1.0, tuple(job.channels)
            )
            thresholds = Thresholds(
                review=0.5, hold=1.0, params=(await self._rules.thresholds(ctx)).params
            )
            state = MemoryState()
            async with self._p.db.tenant_tx(ctx) as conn:
                accounts = await all_accounts(conn)
                profiles = {
                    token: AccountProfile(
                        datetime.combine(a.opened_on, datetime.min.time(), UTC)
                        if a.opened_on
                        else None,
                        a.product,
                        a.kyc_risk,
                        a.party_type,
                    )
                    for token, a in accounts.items()
                }
                labels = {
                    r.source_reference: r.label_group
                    for r in await conn.execute(
                        text(
                            "SELECT source_reference, label_group FROM txn_fraud.label WHERE is_fraud"
                        )
                    )
                }
                default = AccountProfile()
                warm_from = job.from_ts - timedelta(days=HISTORY_DAYS)
                async for row in stream_transactions(conn, warm_from, job.to_ts):
                    attributes = row.attributes or {}
                    balance = attributes.get("balance_after")
                    purpose = attributes.get("purpose")
                    txn = TxnView(
                        ts=row.booked_at,
                        amount=float(row.amount),
                        channel=row.channel,
                        direction=row.direction,
                        status=row.status,
                        account=row.account_token,
                        counterparty=row.counterparty_account_token or row.counterparty_vpa_token,
                        device=row.device_token,
                        ip=row.ip_token,
                        purpose=purpose if isinstance(purpose, str) else None,
                        balance_after=float(balance) if balance not in (None, "") else None,
                        reference=row.source_reference,
                    )
                    snap = state.snapshot_sync("b", txn)
                    counted = row.booked_at >= job.from_ts
                    if counted:
                        outcome = score(
                            txn, snap, profiles.get(txn.account, default), [rule], thresholds
                        )
                        overall.evaluated += 1
                        group = labels.get(txn.reference)
                        if group is not None:
                            overall.labelled += 1
                            groups.setdefault(group, Metrics()).labelled += 1
                        if outcome.recommendation != "allow":
                            overall.flagged += 1
                            if group is not None:
                                overall.true_positives += 1
                                groups[group].true_positives += 1
                    state.apply_sync("b", txn)
        except Exception as exc:
            log.exception("backtest_failed", backtest_id=str(backtest_id))
            error = f"internal error: {type(exc).__name__}"
        has_labels = overall.labelled > 0
        days = max((job.to_ts - job.from_ts).total_seconds() / 86_400, 1 / 24)
        breakdown = {
            name: {
                "labelled": m.labelled,
                "caught": m.true_positives,
                "recall": round(m.true_positives / m.labelled, 4) if m.labelled else None,
            }
            for name, m in sorted(groups.items())
        }
        async with self._p.db.tenant_tx(ctx) as conn:
            import json

            await conn.execute(
                text(
                    "UPDATE txn_fraud.backtest_run SET status = :s, transactions_evaluated = :n, hits = :h, "
                    "true_positives = :tp, false_positives = :fp, false_negatives = :fn, "
                    "precision_value = :p, recall_value = :r, alerts_per_day = :apd, "
                    "breakdown = CAST(:b AS jsonb), error = :e, completed_at = now() WHERE id = :id"
                ),
                {
                    "s": "failed" if error else "completed",
                    "n": overall.evaluated,
                    "h": overall.flagged,
                    "tp": overall.true_positives if has_labels else None,
                    "fp": overall.false_positives if has_labels else None,
                    "fn": overall.false_negatives if has_labels else None,
                    "p": overall.precision if has_labels else None,
                    "r": overall.recall if has_labels else None,
                    "apd": overall.flagged / days,
                    "b": json.dumps(breakdown),
                    "e": error,
                    "id": backtest_id,
                },
            )
            await conn.execute(
                text("UPDATE txn_fraud.backtest_queue SET status = 'done' WHERE backtest_id = :b"),
                {"b": backtest_id},
            )
            await record_audit(
                conn,
                ctx,
                action="rule.backtest.complete",
                resource=f"rule:{job.code}",
                details={
                    "backtest_id": str(backtest_id),
                    "hits": overall.flagged,
                    "status": "failed" if error else "completed",
                },
            )
        return await self.get(ctx, backtest_id)
