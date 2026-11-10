"""OIDC bearer-token authentication and role-based permissions.

Each tenant has its own Keycloak realm. The token's issuer selects the tenant; the signature
is checked against that realm's keys; the token's own `tenant_id` claim must agree.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx
import jwt
from sqlalchemy import text

from sentinel_core.context import ActorType, TenantContext
from sentinel_core.db import Database
from sentinel_core.errors import Forbidden, Unauthenticated

ROLES = frozenset(
    {
        "analyst",
        "investigator",
        "supervisor",
        "compliance_officer",
        "rule_author",
        "rule_approver",
        "model_approver",
        "ciso",
        "tenant_admin",
        "auditor",
        "integration",
    }
)

# Permission -> roles that hold it. `platform_operator` is deliberately absent everywhere:
# operators run the platform and have no access to tenant data.
PERMISSIONS: dict[str, frozenset[str]] = {
    "tenant:read": ROLES,
    "branch:read": ROLES,
    "branch:write": frozenset({"tenant_admin"}),
    "audit:read": frozenset({"auditor", "compliance_officer", "tenant_admin"}),
    "audit:verify": frozenset({"auditor", "tenant_admin"}),
    "pii:reveal": frozenset({"investigator", "supervisor", "compliance_officer"}),
    "ingestion:write": frozenset({"tenant_admin", "integration"}),
    "ingestion:read": frozenset({"tenant_admin", "integration", "auditor", "compliance_officer"}),
    "score:request": frozenset({"integration"}),
    "score:read": frozenset(
        {
            "analyst",
            "investigator",
            "supervisor",
            "compliance_officer",
            "auditor",
            "rule_author",
            "rule_approver",
        }
    ),
    "rule:read": frozenset(
        {
            "rule_author",
            "rule_approver",
            "analyst",
            "investigator",
            "supervisor",
            "compliance_officer",
            "auditor",
        }
    ),
    "rule:author": frozenset({"rule_author"}),
    "rule:approve": frozenset({"rule_approver"}),
    "threshold:write": frozenset({"rule_approver"}),
    "label:write": frozenset({"tenant_admin"}),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: UUID
    roles: frozenset[str]
    branch_ids: tuple[UUID, ...]
    actor_type: ActorType

    def require(self, permission: str) -> None:
        if not self.roles & PERMISSIONS[permission]:
            raise Forbidden(f"missing permission {permission}")

    def context(self, purpose: str = "service_operations") -> TenantContext:
        return TenantContext(
            tenant_id=self.tenant_id,
            actor_id=self.subject,
            actor_type=self.actor_type,
            purpose=purpose,
            branch_scope=self.branch_ids,
        )


class JwksProvider(Protocol):
    async def signing_key(self, jwks_uri: str, kid: str) -> Any: ...


class HttpJwksProvider:
    """Fetches and caches realm signing keys. Refetches once when a key id is unknown."""

    def __init__(self, ttl_seconds: float = 600.0, client: httpx.AsyncClient | None = None) -> None:
        self._ttl = ttl_seconds
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def _fetch(self, jwks_uri: str) -> dict[str, Any]:
        response = await self._client.get(jwks_uri)
        response.raise_for_status()
        keys = {
            k["kid"]: jwt.PyJWK.from_dict(k).key
            for k in response.json().get("keys", [])
            if k.get("use", "sig") == "sig" and "kid" in k
        }
        self._cache[jwks_uri] = (time.monotonic() + self._ttl, keys)
        return keys

    async def signing_key(self, jwks_uri: str, kid: str) -> Any:
        async with self._lock:
            cached = self._cache.get(jwks_uri)
            keys = (
                cached[1]
                if cached and cached[0] > time.monotonic()
                else await self._fetch(jwks_uri)
            )
            if kid not in keys:
                keys = await self._fetch(jwks_uri)
        if kid not in keys:
            raise Unauthenticated("unknown signing key")
        return keys[kid]


@dataclass(frozen=True)
class _Issuer:
    tenant_id: UUID
    jwks_uri: str
    active: bool


class Authenticator:
    def __init__(
        self, db: Database, jwks: JwksProvider, audience: str, issuer_ttl_seconds: float = 60.0
    ) -> None:
        self._db = db
        self._jwks = jwks
        self._audience = audience
        self._ttl = issuer_ttl_seconds
        self._issuers: dict[str, tuple[float, _Issuer]] = {}

    async def _issuer(self, issuer: str) -> _Issuer:
        cached = self._issuers.get(issuer)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        async with self._db.system_tx() as conn:
            row = (
                await conn.execute(
                    text("SELECT tenant_id, jwks_uri FROM core.tenant_issuer WHERE issuer = :i"),
                    {"i": issuer},
                )
            ).first()
        if row is None:
            raise Unauthenticated("unknown issuer")
        ctx = TenantContext(row.tenant_id, "authenticator")
        async with self._db.tenant_tx(ctx) as conn:
            status = (
                await conn.execute(text("SELECT status FROM core.tenant"))
            ).scalar_one_or_none()
        found = _Issuer(row.tenant_id, row.jwks_uri, status == "active")
        self._issuers[issuer] = (time.monotonic() + self._ttl, found)
        return found

    async def authenticate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise Unauthenticated("malformed token") from exc
        issuer_name = unverified.get("iss")
        kid = header.get("kid")
        if not isinstance(issuer_name, str) or not isinstance(kid, str):
            raise Unauthenticated("malformed token")
        issuer = await self._issuer(issuer_name)
        key = await self._jwks.signing_key(issuer.jwks_uri, kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=self._audience,
                issuer=issuer_name,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise Unauthenticated("invalid token") from exc
        if not issuer.active:
            raise Forbidden("tenant is not active")
        return principal_from_claims(claims, issuer.tenant_id)


def principal_from_claims(claims: dict[str, Any], issuer_tenant: UUID) -> Principal:
    try:
        claimed = UUID(str(claims.get("tenant_id")))
        branch_ids = tuple(UUID(str(b)) for b in claims.get("branch_ids") or [])
    except ValueError as exc:
        raise Unauthenticated("invalid tenant or branch claim") from exc
    if claimed != issuer_tenant:
        raise Unauthenticated("token tenant does not match issuer")
    realm_roles = (claims.get("realm_access") or {}).get("roles") or []
    roles = frozenset(str(r) for r in realm_roles) & ROLES
    is_service = "client_id" in claims or str(claims.get("preferred_username", "")).startswith(
        "service-account-"
    )
    return Principal(
        subject=str(claims["sub"]),
        tenant_id=claimed,
        roles=roles,
        branch_ids=branch_ids,
        actor_type="service" if is_service else "user",
    )
