"""Rule storage and lifecycle: versions, maker-checker, activation, thresholds, labels."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sentinel_core.audit import record_audit
from sentinel_core.context import TenantContext
from sentinel_core.errors import Conflict, FieldError, Forbidden, Invalid, NotFound
from sentinel_core.ids import uuid7
from sentinel_core.runtime import Platform
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from sentinel_txn_fraud.domain.expr import InvalidExpression
from sentinel_txn_fraud.domain.features import DEFAULT_PARAMS, PARAMS
from sentinel_txn_fraud.domain.pack import STARTER_PACK
from sentinel_txn_fraud.domain.scoring import RuleSpec, Thresholds

STATES = ("draft", "dry_run", "pending_approval", "active", "retired")
# from -> allowed next states, and the permission each needs
TRANSITIONS: dict[str, dict[str, str]] = {
    "draft": {
        "dry_run": "rule:author",
        "pending_approval": "rule:author",
        "retired": "rule:author",
    },
    "dry_run": {
        "draft": "rule:author",
        "pending_approval": "rule:author",
        "retired": "rule:author",
    },
    "pending_approval": {"active": "rule:approve", "draft": "rule:approve"},
    "active": {"retired": "rule:approve"},
    "retired": {},
}
_VERSION_COLUMNS = (
    "v.id, v.rule_id, r.code, v.version, v.name, v.description, v.expression, v.severity, v.weight, "
    "v.channels, v.state, v.author_id, v.approver_id, v.created_at, v.activated_at"
)


@dataclass(frozen=True)
class RuleVersion:
    id: UUID
    rule_id: UUID
    code: str
    version: int
    name: str
    description: str | None
    expression: Any
    severity: str
    score_weight: float
    applies_to_channels: list[str]
    state: str
    author_id: str
    approver_id: str | None
    created_at: datetime
    activated_at: datetime | None

    @classmethod
    def of(cls, row: Any) -> "RuleVersion":
        return cls(
            row.id,
            row.rule_id,
            row.code,
            row.version,
            row.name,
            row.description,
            row.expression,
            row.severity,
            float(row.weight),
            list(row.channels),
            row.state,
            row.author_id,
            row.approver_id,
            row.created_at,
            row.activated_at,
        )

    def spec(self) -> RuleSpec:
        return RuleSpec(
            self.code,
            self.name,
            self.expression,
            self.severity,
            self.score_weight,
            tuple(self.applies_to_channels),
            self.state == "dry_run",
            str(self.id),
        )


@dataclass(frozen=True)
class Draft:
    name: str
    expression: Any
    severity: str = "medium"
    score_weight: float = 0.5
    description: str | None = None
    applies_to_channels: tuple[str, ...] = ()


def _check(code: str, draft: Draft) -> None:
    try:
        RuleSpec(code, draft.name, draft.expression, draft.severity, draft.score_weight).check()
    except InvalidExpression as exc:
        raise Invalid(
            "the rule expression is not valid",
            [FieldError("expression", message) for message in exc.errors],
        ) from exc
    except ValueError as exc:
        raise Invalid(str(exc)) from exc


class RuleService:
    def __init__(self, platform: Platform) -> None:
        self._p = platform

    # Reading ------------------------------------------------------------------------

    async def list_rules(self, ctx: TenantContext, state: str | None = None) -> list[RuleVersion]:
        """The latest version of every rule, optionally only those in a given state."""
        async with self._p.db.tenant_tx(ctx) as conn:
            rows = await conn.execute(
                text(
                    f"SELECT DISTINCT ON (v.rule_id) {_VERSION_COLUMNS} FROM txn_fraud.rule_version v "  # noqa: S608
                    "JOIN txn_fraud.rule r ON r.id = v.rule_id ORDER BY v.rule_id, v.version DESC"
                )
            )
            versions = [RuleVersion.of(r) for r in rows]
        versions.sort(key=lambda v: v.code)
        return [v for v in versions if state is None or v.state == state]

    async def get(self, ctx: TenantContext, rule_id: UUID, version: int) -> RuleVersion:
        async with self._p.db.tenant_tx(ctx) as conn:
            return await self._get(conn, rule_id, version)

    async def _get(self, conn: AsyncConnection, rule_id: UUID, version: int) -> RuleVersion:
        row = (
            await conn.execute(
                text(
                    f"SELECT {_VERSION_COLUMNS} FROM txn_fraud.rule_version v "  # noqa: S608
                    "JOIN txn_fraud.rule r ON r.id = v.rule_id WHERE v.rule_id = :r AND v.version = :n"
                ),
                {"r": rule_id, "n": version},
            )
        ).first()
        if row is None:
            raise NotFound("rule version not found")
        return RuleVersion.of(row)

    async def live(self, ctx: TenantContext) -> tuple[list[RuleSpec], Thresholds, UUID | None]:
        """What the scorer runs: active and dry-run versions, thresholds, ruleset snapshot id."""
        async with self._p.db.tenant_tx(ctx) as conn:
            rows = await conn.execute(
                text(
                    f"SELECT {_VERSION_COLUMNS} FROM txn_fraud.rule_version v "  # noqa: S608
                    "JOIN txn_fraud.rule r ON r.id = v.rule_id WHERE v.state IN ('active', 'dry_run') "
                    "ORDER BY r.code"
                )
            )
            specs = [RuleVersion.of(r).spec() for r in rows]
            thresholds = await self._thresholds(conn)
            ruleset = (
                await conn.execute(
                    text(
                        "SELECT id FROM txn_fraud.ruleset_version ORDER BY created_at DESC, id DESC LIMIT 1"
                    )
                )
            ).scalar_one_or_none()
        return specs, thresholds, ruleset

    # Writing -------------------------------------------------------------------------

    async def create(self, ctx: TenantContext, code: str, draft: Draft) -> RuleVersion:
        _check(code, draft)
        rule_id = uuid7()
        try:
            async with self._p.db.tenant_tx(ctx) as conn:
                await conn.execute(
                    text("INSERT INTO txn_fraud.rule (id, tenant_id, code) VALUES (:id, :t, :c)"),
                    {"id": rule_id, "t": ctx.tenant_id, "c": code},
                )
                return await self._insert_version(conn, ctx, rule_id, code, 1, draft)
        except IntegrityError as exc:
            raise Conflict(f"a rule with code {code} already exists") from exc

    async def new_version(self, ctx: TenantContext, rule_id: UUID, draft: Draft) -> RuleVersion:
        async with self._p.db.tenant_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT r.code, max(v.version) AS latest FROM txn_fraud.rule r "
                        "JOIN txn_fraud.rule_version v ON v.rule_id = r.id WHERE r.id = :r GROUP BY r.code"
                    ),
                    {"r": rule_id},
                )
            ).first()
            if row is None:
                raise NotFound("rule not found")
            _check(row.code, draft)
            return await self._insert_version(conn, ctx, rule_id, row.code, row.latest + 1, draft)

    async def _insert_version(
        self,
        conn: AsyncConnection,
        ctx: TenantContext,
        rule_id: UUID,
        code: str,
        version: int,
        draft: Draft,
    ) -> RuleVersion:
        version_id = uuid7()
        await conn.execute(
            text(
                "INSERT INTO txn_fraud.rule_version (id, tenant_id, rule_id, version, name, description, "
                "expression, severity, weight, channels, author_id) VALUES (:id, :t, :r, :n, :name, "
                ":d, CAST(:e AS jsonb), :s, :w, :c, :a)"
            ),
            {
                "id": version_id,
                "t": ctx.tenant_id,
                "r": rule_id,
                "n": version,
                "name": draft.name,
                "d": draft.description,
                "e": json.dumps(draft.expression),
                "s": draft.severity,
                "w": draft.score_weight,
                "c": list(draft.applies_to_channels),
                "a": ctx.actor_id,
            },
        )
        await record_audit(
            conn,
            ctx,
            action="rule.version.create",
            resource=f"rule:{code}",
            details={"version": version, "rule_version_id": str(version_id)},
        )
        return await self._get(conn, rule_id, version)

    async def install_starter_pack(self, ctx: TenantContext) -> list[RuleVersion]:
        """Copy the starter rules into the tenant as drafts. Rules that already exist are skipped."""
        created = []
        existing = {v.code for v in await self.list_rules(ctx)}
        for spec in STARTER_PACK:
            if spec.code not in existing:
                created.append(
                    await self.create(
                        ctx,
                        spec.code,
                        Draft(
                            spec.name,
                            spec.expression,
                            spec.severity,
                            spec.weight,
                            None,
                            spec.channels,
                        ),
                    )
                )
        return created

    async def transition(
        self,
        ctx: TenantContext,
        permissions: frozenset[str],
        rule_id: UUID,
        version: int,
        to: str,
        comment: str,
    ) -> RuleVersion:
        """Move a version through its lifecycle.

        Activation needs a completed backtest of this exact version and an approver who is not
        its author. Activating a version retires the rule's previously active version.
        """
        if to not in STATES:
            raise Invalid(f"state must be one of {', '.join(STATES)}")
        async with self._p.db.tenant_tx(ctx) as conn:
            current = await self._get(conn, rule_id, version)
            needed = TRANSITIONS[current.state].get(to)
            if needed is None:
                raise Conflict(f"a {current.state} rule version cannot become {to}")
            if needed not in permissions:
                raise Forbidden(f"missing permission {needed}")
            params: dict[str, Any] = {"id": current.id, "to": to}
            extra = ""
            if to == "active":
                if current.author_id == ctx.actor_id:
                    raise Forbidden("a rule cannot be approved by its author")
                tested = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM txn_fraud.backtest_run WHERE rule_version_id = :id "
                            "AND status = 'completed' LIMIT 1"
                        ),
                        {"id": current.id},
                    )
                ).first()
                if tested is None:
                    raise Conflict(
                        "a completed backtest of this version is required before activation"
                    )
                await conn.execute(
                    text(
                        "UPDATE txn_fraud.rule_version SET state = 'retired', retired_at = now() "
                        "WHERE rule_id = :r AND state = 'active'"
                    ),
                    {"r": rule_id},
                )
                extra = ", approver_id = :by, activated_at = now()"
                params["by"] = ctx.actor_id
            elif to == "retired":
                extra = ", retired_at = now()"
            await conn.execute(
                text(f"UPDATE txn_fraud.rule_version SET state = :to{extra} WHERE id = :id"),  # noqa: S608
                params,
            )
            if "active" in (to, current.state):
                await self._snapshot_ruleset(conn, ctx)
            await record_audit(
                conn,
                ctx,
                action="rule.version.transition",
                resource=f"rule:{current.code}",
                details={"version": version, "from": current.state, "to": to, "comment": comment},
            )
            return await self._get(conn, rule_id, version)

    async def _snapshot_ruleset(self, conn: AsyncConnection, ctx: TenantContext) -> None:
        ids = [
            r.id
            for r in await conn.execute(
                text("SELECT id FROM txn_fraud.rule_version WHERE state = 'active' ORDER BY id")
            )
        ]
        await conn.execute(
            text(
                "INSERT INTO txn_fraud.ruleset_version (id, tenant_id, rule_version_ids, created_by) "
                "VALUES (:id, :t, :ids, :by)"
            ),
            {"id": uuid7(), "t": ctx.tenant_id, "ids": ids, "by": ctx.actor_id},
        )

    # Thresholds -----------------------------------------------------------------------

    async def _thresholds(self, conn: AsyncConnection) -> Thresholds:
        row = (
            await conn.execute(
                text("SELECT review, hold, params, computed FROM txn_fraud.threshold_profile")
            )
        ).first()
        if row is None:
            return Thresholds()
        params = {
            **DEFAULT_PARAMS,
            **{k: float(v) for k, v in row.computed.items()},
            **{k: float(v) for k, v in row.params.items()},
        }
        return Thresholds(float(row.review), float(row.hold), params)

    async def thresholds(self, ctx: TenantContext) -> Thresholds:
        async with self._p.db.tenant_tx(ctx) as conn:
            return await self._thresholds(conn)

    async def set_thresholds(
        self, ctx: TenantContext, review: float, hold: float, params: dict[str, float]
    ) -> Thresholds:
        unknown = set(params) - set(PARAMS)
        if unknown:
            raise Invalid(f"unknown parameters: {', '.join(sorted(unknown))}")
        if not 0 < review <= hold <= 1:
            raise Invalid("thresholds must satisfy 0 < review <= hold <= 1")
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO txn_fraud.threshold_profile (tenant_id, review, hold, params, updated_by) "
                    "VALUES (:t, :r, :h, CAST(:p AS jsonb), :by) ON CONFLICT (tenant_id) DO UPDATE SET "
                    "review = EXCLUDED.review, hold = EXCLUDED.hold, params = EXCLUDED.params, "
                    "updated_by = EXCLUDED.updated_by, updated_at = now()"
                ),
                {
                    "t": ctx.tenant_id,
                    "r": review,
                    "h": hold,
                    "p": json.dumps(params),
                    "by": ctx.actor_id,
                },
            )
            await record_audit(
                conn,
                ctx,
                action="threshold.update",
                resource="thresholds",
                details={"review": review, "hold": hold, "params": params},
            )
            return await self._thresholds(conn)

    async def set_computed(self, ctx: TenantContext, computed: dict[str, float]) -> Thresholds:
        """Store thresholds derived from the tenant's own data. Explicit parameters win over them."""
        async with self._p.db.tenant_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO txn_fraud.threshold_profile (tenant_id, computed, updated_by) "
                    "VALUES (:t, CAST(:c AS jsonb), :by) ON CONFLICT (tenant_id) DO UPDATE SET "
                    "computed = EXCLUDED.computed, updated_by = EXCLUDED.updated_by, updated_at = now()"
                ),
                {"t": ctx.tenant_id, "c": json.dumps(computed), "by": ctx.actor_id},
            )
            await record_audit(
                conn,
                ctx,
                action="threshold.recompute",
                resource="thresholds",
                details={"computed": computed},
            )
            return await self._thresholds(conn)

    # Labels ------------------------------------------------------------------------------

    async def load_labels(
        self, ctx: TenantContext, rows: list[tuple[str, str, bool]], origin: str = "synthetic"
    ) -> int:
        """Store ground truth as (reference, group, is_fraud). Replaces earlier labels for a reference."""
        if not rows:
            return 0
        unique = {ref: (group, fraud) for ref, group, fraud in rows}
        async with self._p.db.tenant_tx(ctx) as conn:
            for start in range(0, len(unique), 5_000):
                chunk = list(unique.items())[start : start + 5_000]
                await conn.execute(
                    text(
                        "INSERT INTO txn_fraud.label (tenant_id, source_reference, label_group, is_fraud, "
                        "origin) VALUES (:t, :ref, :g, :f, :o) ON CONFLICT (tenant_id, source_reference) "
                        "DO UPDATE SET label_group = EXCLUDED.label_group, is_fraud = EXCLUDED.is_fraud, "
                        "origin = EXCLUDED.origin"
                    ),
                    [
                        {"t": ctx.tenant_id, "ref": ref, "g": group, "f": fraud, "o": origin}
                        for ref, (group, fraud) in chunk
                    ],
                )
            await record_audit(
                conn,
                ctx,
                action="label.load",
                resource="labels",
                details={"count": len(unique), "origin": origin},
            )
        return len(unique)
