"""HTTP endpoints: scoring, rules, backtests, thresholds, labels."""

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sentinel_core.auth import PERMISSIONS, Principal
from sentinel_core.errors import Invalid, NotFound
from sentinel_core.pagination import decode_cursor, encode_cursor
from sentinel_core.web import PlatformDep, PrincipalDep
from sentinel_ingestion.public import TransactionInput, amount_percentile

from sentinel_txn_fraud.backtests import Backtest, BacktestService
from sentinel_txn_fraud.domain.features import FEATURES, FIELDS, PARAMS
from sentinel_txn_fraud.rules import Draft, RuleService, RuleVersion
from sentinel_txn_fraud.runtime import ScoringService, reasons_json

score_router = APIRouter(tags=["scoring"])
router = APIRouter(tags=["rules"])
PURPOSE = "fraud_prevention"


def get_scoring(request: Request) -> ScoringService:
    service: ScoringService = request.app.state.scoring
    return service


def get_rules(request: Request) -> RuleService:
    service: RuleService = request.app.state.rules
    return service


def get_backtests(request: Request) -> BacktestService:
    service: BacktestService = request.app.state.backtests
    return service


ScoringDep = Annotated[ScoringService, Depends(get_scoring)]
RulesDep = Annotated[RuleService, Depends(get_rules)]
BacktestsDep = Annotated[BacktestService, Depends(get_backtests)]


def _permissions(principal: Principal) -> frozenset[str]:
    return frozenset(name for name, roles in PERMISSIONS.items() if principal.roles & roles)


# Scoring ------------------------------------------------------------------------------------


class ReasonOut(BaseModel):
    kind: str
    code: str
    rule_version_id: UUID | None = None
    contribution: float
    observed: dict[str, Any]
    dry_run: bool = False


class ScoreOut(BaseModel):
    score_id: UUID
    transaction_id: UUID | None = None
    score: float
    recommendation: str
    reasons: list[ReasonOut]
    ruleset_version_id: UUID | None = None
    model_version_id: UUID | None = None
    degraded: bool
    latency_ms: float | None = None


@score_router.post("/score", response_model=ScoreOut)
async def score_transaction(
    principal: PrincipalDep, scoring: ScoringDep, body: TransactionInput
) -> ScoreOut:
    """Score a transaction. The answer is advice: Sentinel takes no action on funds."""
    principal.require("score:request")
    if principal.branch_ids:
        raise Invalid("scoring requires institution-wide access")
    scored = await scoring.score_request(principal.context(PURPOSE), body)
    return ScoreOut(
        score_id=scored.score_id,
        score=scored.outcome.score,
        recommendation=scored.outcome.recommendation,
        reasons=[ReasonOut.model_validate(r) for r in reasons_json(scored.outcome)],
        ruleset_version_id=scored.ruleset_version_id,
        degraded=scored.degraded,
        latency_ms=round(scored.latency_ms, 3),
    )


@router.get("/scores/{score_id}", response_model=ScoreOut, tags=["scoring"])
async def get_score(principal: PrincipalDep, scoring: ScoringDep, score_id: UUID) -> ScoreOut:
    principal.require("score:read")
    row = await scoring.get(principal.context(PURPOSE), score_id)
    if row is None:
        raise NotFound("score not found")
    return ScoreOut.model_validate(row, from_attributes=True)


# Rules ---------------------------------------------------------------------------------------


class RuleDraftIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    applies_to_channels: list[str] = Field(default_factory=list)
    expression: Any
    severity: str = "medium"
    score_weight: float = Field(default=0.5, gt=0, le=1)

    def draft(self) -> Draft:
        return Draft(
            self.name,
            self.expression,
            self.severity,
            self.score_weight,
            self.description,
            tuple(self.applies_to_channels),
        )


class RuleCreateIn(RuleDraftIn):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,31}$")


class RuleVersionOut(BaseModel):
    rule_id: UUID
    code: str
    version: int
    name: str
    description: str | None
    applies_to_channels: list[str]
    expression: Any
    severity: str
    score_weight: float
    state: str
    author_id: str
    approver_id: str | None
    activated_at: datetime | None

    @classmethod
    def of(cls, version: RuleVersion) -> "RuleVersionOut":
        return cls.model_validate(version, from_attributes=True)


class RuleOut(BaseModel):
    id: UUID
    name: str
    code: str
    current_version: RuleVersionOut


class RulePage(BaseModel):
    items: list[RuleOut]
    next_cursor: str | None


@router.get("/rules", response_model=RulePage)
async def list_rules(
    principal: PrincipalDep,
    rules: RulesDep,
    state: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RulePage:
    principal.require("rule:read")
    after = decode_cursor(cursor, 1)
    versions = [
        v
        for v in await rules.list_rules(principal.context(PURPOSE), state)
        if after is None or v.code > after[0]
    ]
    page = versions[:limit]
    return RulePage(
        items=[
            RuleOut(id=v.rule_id, name=v.name, code=v.code, current_version=RuleVersionOut.of(v))
            for v in page
        ],
        next_cursor=encode_cursor([page[-1].code]) if len(versions) > limit else None,
    )


@router.post("/rules", status_code=status.HTTP_201_CREATED, response_model=RuleOut)
async def create_rule(principal: PrincipalDep, rules: RulesDep, body: RuleCreateIn) -> RuleOut:
    principal.require("rule:author")
    version = await rules.create(principal.context(PURPOSE), body.code, body.draft())
    return RuleOut(
        id=version.rule_id,
        name=version.name,
        code=version.code,
        current_version=RuleVersionOut.of(version),
    )


@router.post(
    "/rules/starter-pack", status_code=status.HTTP_201_CREATED, response_model=list[RuleVersionOut]
)
async def install_starter_pack(principal: PrincipalDep, rules: RulesDep) -> list[RuleVersionOut]:
    """Copy the starter rules into this tenant as drafts. Existing rule codes are left alone."""
    principal.require("rule:author")
    return [
        RuleVersionOut.of(v) for v in await rules.install_starter_pack(principal.context(PURPOSE))
    ]


class VocabularyEntry(BaseModel):
    name: str
    type: str
    description: str


class VocabularyOut(BaseModel):
    fields: list[VocabularyEntry]
    features: list[VocabularyEntry]
    params: list[VocabularyEntry]
    operations: list[str]


@router.get("/rules/vocabulary", response_model=VocabularyOut)
async def get_vocabulary(principal: PrincipalDep) -> VocabularyOut:
    """Everything a rule may refer to. Drives the rule builder."""
    principal.require("rule:read")
    from sentinel_txn_fraud.domain.expr import OPS

    def entries(table: dict[str, tuple[str, str]]) -> list[VocabularyEntry]:
        return [VocabularyEntry(name=n, type=t, description=d) for n, (t, d) in table.items()]

    return VocabularyOut(
        fields=entries(FIELDS),
        features=entries(FEATURES),
        params=entries(PARAMS),
        operations=sorted(OPS),
    )


@router.post(
    "/rules/{rule_id}/versions", status_code=status.HTTP_201_CREATED, response_model=RuleVersionOut
)
async def create_rule_version(
    principal: PrincipalDep, rules: RulesDep, rule_id: UUID, body: RuleDraftIn
) -> RuleVersionOut:
    principal.require("rule:author")
    return RuleVersionOut.of(
        await rules.new_version(principal.context(PURPOSE), rule_id, body.draft())
    )


class TransitionIn(BaseModel):
    to: str
    comment: str = Field(min_length=1, max_length=1000)


@router.post("/rules/{rule_id}/versions/{version}/transitions", response_model=RuleVersionOut)
async def transition_rule_version(
    principal: PrincipalDep,
    rules: RulesDep,
    scoring: ScoringDep,
    rule_id: UUID,
    version: Annotated[int, Field(ge=1)],
    body: TransitionIn,
) -> RuleVersionOut:
    principal.require("rule:read")
    updated = await rules.transition(
        principal.context(PURPOSE), _permissions(principal), rule_id, version, body.to, body.comment
    )
    scoring.invalidate(principal.tenant_id)
    return RuleVersionOut.of(updated)


# Backtests -----------------------------------------------------------------------------------


class BacktestIn(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime


class BacktestOut(BaseModel):
    id: UUID
    rule_id: UUID
    version: int
    status: str
    from_: datetime = Field(serialization_alias="from")
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
    def of(cls, backtest: Backtest) -> "BacktestOut":
        return cls.model_validate(backtest, from_attributes=True)


@router.post(
    "/rules/{rule_id}/versions/{version}/backtests",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BacktestOut,
)
async def start_backtest(
    principal: PrincipalDep,
    backtests: BacktestsDep,
    rule_id: UUID,
    version: Annotated[int, Field(ge=1)],
    body: BacktestIn,
) -> BacktestOut:
    if not principal.roles & (PERMISSIONS["rule:author"] | PERMISSIONS["rule:approve"]):
        principal.require("rule:author")
    job = await backtests.start(principal.context(PURPOSE), rule_id, version, body.from_, body.to)
    return BacktestOut.of(job)


@router.get("/backtests/{backtest_id}", response_model=BacktestOut)
async def get_backtest(
    principal: PrincipalDep, backtests: BacktestsDep, backtest_id: UUID
) -> BacktestOut:
    principal.require("rule:read")
    return BacktestOut.of(await backtests.get(principal.context(PURPOSE), backtest_id))


# Thresholds ----------------------------------------------------------------------------------


class ThresholdsBody(BaseModel):
    review: float = Field(gt=0, le=1)
    hold: float = Field(gt=0, le=1)
    params: dict[str, float] = Field(default_factory=dict)


@router.get("/thresholds", response_model=ThresholdsBody, tags=["rules"])
async def get_thresholds(principal: PrincipalDep, rules: RulesDep) -> ThresholdsBody:
    principal.require("rule:read")
    current = await rules.thresholds(principal.context(PURPOSE))
    return ThresholdsBody(review=current.review, hold=current.hold, params=dict(current.params))


@router.put("/thresholds", response_model=ThresholdsBody, tags=["rules"])
async def put_thresholds(
    principal: PrincipalDep, rules: RulesDep, scoring: ScoringDep, body: ThresholdsBody
) -> ThresholdsBody:
    principal.require("threshold:write")
    current = await rules.set_thresholds(
        principal.context(PURPOSE), body.review, body.hold, body.params
    )
    scoring.invalidate(principal.tenant_id)
    return ThresholdsBody(review=current.review, hold=current.hold, params=dict(current.params))


@router.post("/thresholds/recompute", response_model=ThresholdsBody, tags=["rules"])
async def recompute_thresholds(
    principal: PrincipalDep,
    platform: PlatformDep,
    rules: RulesDep,
    scoring: ScoringDep,
    as_of: datetime | None = None,
) -> ThresholdsBody:
    """Derive thresholds from this tenant's own last 30 days of transactions.

    `large_amount` becomes the 99th percentile of completed outgoing transfers. A value set
    explicitly with PUT /thresholds still takes precedence.
    """
    principal.require("threshold:write")
    ctx = principal.context(PURPOSE)
    end = as_of or datetime.now(UTC)
    async with platform.db.tenant_tx(ctx) as conn:
        p99 = await amount_percentile(conn, 0.99, end - timedelta(days=30), end)
    computed = {"large_amount": round(p99, 2)} if p99 else {}
    current = await rules.set_computed(ctx, computed)
    scoring.invalidate(principal.tenant_id)
    return ThresholdsBody(review=current.review, hold=current.hold, params=dict(current.params))


# Labels --------------------------------------------------------------------------------------


class LabelsOut(BaseModel):
    loaded: int


@router.post("/labels", response_model=LabelsOut, tags=["rules"])
async def upload_labels(
    principal: PrincipalDep, rules: RulesDep, file: Annotated[UploadFile, File()]
) -> LabelsOut:
    """Load ground truth for backtests: a CSV with source_reference, scenario_type, is_fraud.

    For evaluation on synthetic data. In operation, labels come from investigators' decisions.
    """
    principal.require("label:write")
    try:
        reader = csv.DictReader(io.StringIO((await file.read()).decode("utf-8-sig")))
        needed = {"source_reference", "scenario_type", "is_fraud"}
        if not needed <= set(reader.fieldnames or []):
            raise Invalid(f"the file needs the columns {', '.join(sorted(needed))}")
        rows = [
            (r["source_reference"], r["scenario_type"], r["is_fraud"].strip().lower() == "true")
            for r in reader
            if r["source_reference"]
        ]
    except UnicodeDecodeError as exc:
        raise Invalid("the file is not UTF-8") from exc
    return LabelsOut(loaded=await rules.load_labels(principal.context(PURPOSE), rows))
