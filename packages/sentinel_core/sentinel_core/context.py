"""The tenant context every database transaction runs under."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ActorType = Literal["user", "service"]

PURPOSES = frozenset(
    {"fraud_prevention", "aml_compliance", "kyc", "security_monitoring", "service_operations"}
)


@dataclass(frozen=True)
class TenantContext:
    """Who is acting, for which tenant, within which branches, and why.

    `branch_scope` empty means institution-wide.
    """

    tenant_id: UUID
    actor_id: str
    actor_type: ActorType = "service"
    purpose: str = "service_operations"
    branch_scope: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.purpose not in PURPOSES:
            raise ValueError(f"unknown purpose: {self.purpose}")

    @property
    def institution_wide(self) -> bool:
        return not self.branch_scope

    def with_purpose(self, purpose: str) -> "TenantContext":
        return TenantContext(
            self.tenant_id, self.actor_id, self.actor_type, purpose, self.branch_scope
        )
