"""The scoring runtime: turns a transaction into a stored, explained score.

Two entry points share one implementation:

- `score_request`: a bank asks before or as a payment happens. Reads state, does not change it,
  and returns a recommendation. Raises no alert: the payment, if attempted, arrives through
  ingestion and is scored again there.
- `score_events`: every ingested transaction, from the event stream. Scores, then updates the
  state, and raises an alert when the score reaches the review threshold.

If the online state is unreachable, scoring continues on the transaction's own fields and the
result is marked `degraded`. It never fails open silently.
"""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sentinel_core.context import TenantContext
from sentinel_core.events import build_event
from sentinel_core.ids import uuid7
from sentinel_core.logging import get_logger
from sentinel_core.outbox import add_many_to_outbox
from sentinel_core.runtime import Platform
from sentinel_ingestion.public import AccountInfo, TransactionInput, accounts_by_token
from sqlalchemy import text

from sentinel_txn_fraud.domain.features import AccountProfile, Snapshot, TxnView
from sentinel_txn_fraud.domain.scoring import SEVERITIES, RuleSpec, ScoreOutcome, Thresholds, score
from sentinel_txn_fraud.domain.state import StateStore
from sentinel_txn_fraud.rules import RuleService

log = get_logger(__name__)
SCORE_TOPIC = "sentinel.txn_fraud.score.v1"
ALERT_TOPIC = "sentinel.cases.alert.v1"
PRODUCER = "txn_fraud"
RULES_TTL = 5.0
PROFILE_TTL = 600.0


@dataclass
class Scored:
    score_id: UUID
    transaction_id: UUID | None
    outcome: ScoreOutcome
    ruleset_version_id: UUID | None
    degraded: bool
    latency_ms: float


@dataclass
class _Live:
    expires: float
    rules: list[RuleSpec]
    thresholds: Thresholds
    ruleset: UUID | None


def reasons_json(outcome: ScoreOutcome) -> list[dict[str, Any]]:
    return [
        {
            "kind": r.kind,
            "code": r.code,
            "rule_version_id": r.rule_version_id,
            "contribution": r.contribution,
            "observed": r.observed,
            "dry_run": r.dry_run,
        }
        for r in outcome.reasons
    ]


def _profile(info: AccountInfo | None) -> AccountProfile:
    if info is None:
        return AccountProfile()
    opened = datetime.combine(info.opened_on, datetime.min.time(), UTC) if info.opened_on else None
    return AccountProfile(opened, info.product, info.kyc_risk, info.party_type)


class ScoringService:
    def __init__(
        self, platform: Platform, state: StateStore, rules: RuleService | None = None
    ) -> None:
        self._p = platform
        self._state = state
        self._rules = rules or RuleService(platform)
        self._live: dict[UUID, _Live] = {}
        self._accounts: dict[tuple[UUID, str], tuple[float, AccountInfo | None]] = {}
        self._severity: dict[str, str] = {}

    def invalidate(self, tenant_id: UUID | None = None) -> None:
        """Drop cached rules so the next score reads them afresh."""
        if tenant_id is None:
            self._live.clear()
        else:
            self._live.pop(tenant_id, None)

    async def _ruleset(self, ctx: TenantContext) -> _Live:
        live = self._live.get(ctx.tenant_id)
        if live is None or live.expires < time.monotonic():
            rules, thresholds, ruleset = await self._rules.live(ctx)
            live = _Live(time.monotonic() + RULES_TTL, rules, thresholds, ruleset)
            self._live[ctx.tenant_id] = live
            self._severity.update({rule.code: rule.severity for rule in rules})
        return live

    async def _account_infos(
        self, ctx: TenantContext, tokens: Sequence[str]
    ) -> dict[str, AccountInfo | None]:
        now = time.monotonic()
        found: dict[str, AccountInfo | None] = {}
        missing = []
        for token in set(tokens):
            cached = self._accounts.get((ctx.tenant_id, token))
            if cached and cached[0] > now:
                found[token] = cached[1]
            else:
                missing.append(token)
        if missing:
            async with self._p.db.tenant_tx(ctx) as conn:
                loaded = await accounts_by_token(conn, missing)
            for token in missing:
                found[token] = loaded.get(token)
                # Unknown accounts are not cached: they may be ingested a moment later.
                if token in loaded:
                    self._accounts[(ctx.tenant_id, token)] = (now + PROFILE_TTL, loaded[token])
        return found

    async def _snapshot(self, tenant: str, txn: TxnView) -> tuple[Snapshot, bool]:
        try:
            return await self._state.snapshot(tenant, txn), False
        except Exception:
            log.exception("state_unavailable")
            return Snapshot(), True

    # Entry points -----------------------------------------------------------------------

    async def score_request(self, ctx: TenantContext, request: TransactionInput) -> Scored:
        started = time.perf_counter()
        # Tokens are computed, not stored: a scoring request writes nothing to the vault.
        token = self._p.tokeniser.token_for
        account = await token(ctx, "acct", request.account_number)
        counterparty = None
        if request.counterparty_account_number:
            counterparty = await token(ctx, "acct", request.counterparty_account_number)
        elif request.counterparty_vpa:
            counterparty = await token(ctx, "vpa", request.counterparty_vpa)
        device = await token(ctx, "dev", request.device_id) if request.device_id else None
        ip = await token(ctx, "ip", request.ip_address) if request.ip_address else None
        balance = request.attributes.get("balance_after")
        txn = TxnView(
            ts=request.booked_at,
            amount=float(request.amount.amount),
            channel=request.channel,
            direction=request.direction,
            status=request.status,
            account=account,
            counterparty=counterparty,
            device=device,
            ip=ip,
            purpose=_text(request.attributes.get("purpose")),
            balance_after=float(balance)
            if isinstance(balance, int | float | str) and balance != ""
            else None,
            reference=request.source_reference,
        )
        live = await self._ruleset(ctx)
        info = (await self._account_infos(ctx, [txn.account]))[txn.account]
        snap, degraded = await self._snapshot(str(ctx.tenant_id), txn)
        outcome = score(txn, snap, _profile(info), live.rules, live.thresholds)
        scored = Scored(uuid7(), None, outcome, live.ruleset, degraded, 0.0)
        scored.latency_ms = (time.perf_counter() - started) * 1000
        await self._persist(ctx, [(txn, scored, info)], mode="realtime", alerts=False)
        scored.latency_ms = (time.perf_counter() - started) * 1000
        return scored

    async def score_events(
        self, ctx: TenantContext, payloads: Sequence[dict[str, Any]]
    ) -> list[Scored]:
        """Score ingested transactions in order, updating state after each."""
        if not payloads:
            return []
        live = await self._ruleset(ctx)
        infos = await self._account_infos(ctx, [p["account_token"] for p in payloads])
        tenant = str(ctx.tenant_id)
        done: list[tuple[TxnView, Scored, AccountInfo | None]] = []
        first_time = getattr(self._state, "first_time", None)
        for payload in payloads:
            started = time.perf_counter()
            txn = view_of_event(payload)
            if first_time is not None and not await first_time(tenant, payload["transaction_id"]):
                continue  # already scored on an earlier delivery
            snap, degraded = await self._snapshot(tenant, txn)
            info = infos.get(txn.account)
            outcome = score(txn, snap, _profile(info), live.rules, live.thresholds)
            try:
                await self._state.apply(tenant, txn)
            except Exception:
                log.exception("state_unavailable")
                degraded = True
            latency = (time.perf_counter() - started) * 1000
            done.append(
                (
                    txn,
                    Scored(
                        uuid7(),
                        UUID(payload["transaction_id"]),
                        outcome,
                        live.ruleset,
                        degraded,
                        latency,
                    ),
                    info,
                )
            )
        await self._persist(ctx, done, mode="stream", alerts=True)
        return [scored for _, scored, _ in done]

    # Persistence --------------------------------------------------------------------------

    async def _persist(
        self,
        ctx: TenantContext,
        items: Sequence[tuple[TxnView, Scored, AccountInfo | None]],
        *,
        mode: str,
        alerts: bool,
    ) -> None:
        if not items:
            return
        rows, score_events, alert_events = [], [], []
        for txn, scored, info in items:
            outcome = scored.outcome
            branch_id = info.branch_id if info else None
            reasons = reasons_json(outcome)
            rows.append(
                {
                    "id": scored.score_id,
                    "t": ctx.tenant_id,
                    "b": branch_id,
                    "txn": scored.transaction_id,
                    "ref": txn.reference,
                    "acct": txn.account,
                    "mode": mode,
                    "score": outcome.score,
                    "rec": outcome.recommendation,
                    "rs": scored.ruleset_version_id,
                    "deg": scored.degraded,
                    "reasons": json.dumps(reasons),
                    "ms": scored.latency_ms,
                }
            )
            if scored.transaction_id is None or scored.ruleset_version_id is None:
                continue  # the event contract needs both; realtime scores are returned to the caller
            key = f"{ctx.tenant_id}:{txn.account}"
            score_events.append(
                (
                    key,
                    build_event(
                        ctx,
                        event_type="txn_fraud.score.computed",
                        producer=PRODUCER,
                        branch_id=branch_id,
                        payload={
                            "score_id": str(scored.score_id),
                            "transaction_id": str(scored.transaction_id),
                            "score": outcome.score,
                            "recommendation": outcome.recommendation,
                            "ruleset_version_id": str(scored.ruleset_version_id),
                            "model_version_id": None,
                            "degraded": scored.degraded,
                            "latency_ms": round(scored.latency_ms, 3),
                            "reasons": [
                                {k: v for k, v in r.items() if k != "dry_run"}
                                for r in reasons
                                if not r["dry_run"]
                            ],
                        },
                    ),
                )
            )
            fired = outcome.fired
            if alerts and outcome.recommendation != "allow" and fired:
                top = max(
                    fired, key=lambda code: SEVERITIES.index(self._severity.get(code, "medium"))
                )
                alert_events.append(
                    (
                        key,
                        build_event(
                            ctx,
                            event_type="txn_fraud.alert.raised",
                            producer=PRODUCER,
                            branch_id=branch_id,
                            payload={
                                "alert_id": str(uuid7()),
                                "source_module": "txn_fraud",
                                "alert_type": top,
                                "severity": self._severity.get(top, "medium"),
                                "priority_score": outcome.score,
                                "subject": {
                                    "kind": "transaction",
                                    "ref": str(scored.transaction_id),
                                },
                                "score_id": str(scored.score_id),
                                "summary_code": f"txn_fraud.{top}",
                                "evidence_refs": [
                                    f"score:{scored.score_id}",
                                    f"transaction:{scored.transaction_id}",
                                    f"account:{txn.account}",
                                ],
                            },
                        ),
                    )
                )
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO txn_fraud.score (id, tenant_id, branch_id, transaction_id, source_reference, "
                    "account_token, mode, score, recommendation, ruleset_version_id, degraded, reasons, "
                    "latency_ms) VALUES (:id, :t, :b, :txn, :ref, :acct, :mode, :score, :rec, :rs, :deg, "
                    "CAST(:reasons AS jsonb), :ms) ON CONFLICT DO NOTHING"
                ),
                rows,
            )
            await add_many_to_outbox(conn, topic=SCORE_TOPIC, events=score_events)
            await add_many_to_outbox(conn, topic=ALERT_TOPIC, events=alert_events)

    async def get(self, ctx: TenantContext, score_id: UUID) -> Any:
        async with self._p.db.tenant_tx(ctx) as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT id AS score_id, transaction_id, score, recommendation, reasons, "
                        "ruleset_version_id, degraded, latency_ms, mode, source_reference, created_at "
                        "FROM txn_fraud.score WHERE id = :id"
                    ),
                    {"id": score_id},
                )
            ).first()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def view_of_event(payload: dict[str, Any]) -> TxnView:
    """The transaction in an `ingestion.transaction.received` event."""
    attributes = payload.get("attributes") or {}
    balance = attributes.get("balance_after")
    return TxnView(
        ts=datetime.fromisoformat(payload["booked_at"]),
        amount=float(payload["amount"]["amount"]),
        channel=payload["channel"],
        direction=payload["direction"],
        status=payload["status"],
        account=payload["account_token"],
        counterparty=payload.get("counterparty_account_token")
        or payload.get("counterparty_vpa_token"),
        device=payload.get("device_token"),
        ip=payload.get("ip_token"),
        purpose=_text(attributes.get("purpose")),
        balance_after=float(balance) if balance not in (None, "") else None,
        reference=payload.get("source_reference", ""),
        transaction_id=payload.get("transaction_id"),
    )
