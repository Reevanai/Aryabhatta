"""Generator configuration and named size profiles."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Institution = Literal["urban_cooperative_bank", "nbfc"]
SCENARIO_TYPES = (
    "mule_chain",
    "fraud_ring",
    "layering",
    "circular_flow",
    "structuring",
    "account_takeover",
    "app_scam",
    "synthetic_identity",
    "loan_fraud",
)
# Scenarios that need deposit accounts and payment rails; an NBFC has neither.
BANK_ONLY = frozenset(
    {"mule_chain", "layering", "circular_flow", "structuring", "account_takeover", "app_scam"}
)


class ScenarioCounts(BaseModel):
    """How many instances of each fraud scenario to inject."""

    model_config = ConfigDict(frozen=True)

    mule_chain: int = Field(default=6, ge=0)
    fraud_ring: int = Field(default=2, ge=0)
    layering: int = Field(default=3, ge=0)
    circular_flow: int = Field(default=3, ge=0)
    structuring: int = Field(default=4, ge=0)
    account_takeover: int = Field(default=6, ge=0)
    app_scam: int = Field(default=8, ge=0)
    synthetic_identity: int = Field(default=3, ge=0)
    loan_fraud: int = Field(default=4, ge=0)

    def scaled(self, factor: float) -> "ScenarioCounts":
        return ScenarioCounts(
            **{name: max(1, round(value * factor)) for name, value in self.model_dump().items()}
        )


class SynthConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed: int = 20261110
    institution: Institution = "urban_cooperative_bank"
    customers: int = Field(default=1500, ge=50)
    branches: int = Field(default=4, ge=1, le=200)
    days: int = Field(default=45, ge=14, le=730)
    end_date: date = date(2026, 10, 31)
    scenarios: ScenarioCounts = ScenarioCounts()
    # Legitimate activity that resembles fraud, so detection precision is a real measurement.
    hard_negatives: int = Field(default=12, ge=0)
    # Amount below which the structuring scenario keeps each cash deposit. A generator
    # parameter, not a statement of any regulatory threshold.
    structuring_threshold: int = Field(default=50_000, gt=1_000)
    failed_rate: float = Field(default=0.03, ge=0, le=0.5)


PROFILES: dict[str, SynthConfig] = {
    "tiny": SynthConfig(
        customers=300,
        branches=2,
        days=30,
        hard_negatives=6,
        scenarios=ScenarioCounts(
            mule_chain=2,
            fraud_ring=1,
            layering=1,
            circular_flow=1,
            structuring=2,
            account_takeover=2,
            app_scam=3,
            synthetic_identity=1,
            loan_fraud=2,
        ),
    ),
    "demo": SynthConfig(),
    "small": SynthConfig(
        customers=5000,
        branches=8,
        days=90,
        hard_negatives=40,
        scenarios=ScenarioCounts().scaled(3),
    ),
    "medium": SynthConfig(
        customers=30000,
        branches=25,
        days=180,
        hard_negatives=200,
        scenarios=ScenarioCounts().scaled(15),
    ),
}


def profile(name: str, **overrides: object) -> SynthConfig:
    if name not in PROFILES:
        raise KeyError(f"unknown profile {name!r}; choose from {', '.join(PROFILES)}")
    return PROFILES[name].model_copy(update=overrides)
