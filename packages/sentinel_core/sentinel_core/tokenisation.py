"""PII tokenisation.

A token is a keyed HMAC of the normalised value, per tenant and field type. The same value
always gives the same token inside one tenant, so joins and counters work on tokens, and
tokens from different tenants cannot be correlated. The clear value is stored encrypted in
the vault schema, which only this module's database role can reach.
"""

import base64
import hashlib
import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import text

from sentinel_core.audit import record_audit
from sentinel_core.context import TenantContext
from sentinel_core.crypto import decrypt, encrypt
from sentinel_core.db import Database
from sentinel_core.errors import Forbidden, Invalid, NotFound
from sentinel_core.tenant_keys import TenantKeys

FIELD_TYPES = frozenset(
    {"cust", "name", "pan", "phone", "email", "addr", "dob", "acct", "vpa", "dev", "ip"}
)
TOKEN_PATTERN = re.compile(r"^tok_([a-z]+)_[A-Za-z0-9_-]{16,}$")
_SPACES = re.compile(r"\s+")
_NON_DIGITS = re.compile(r"[^0-9]")


def normalise(field_type: str, value: str) -> str:
    """Canonical form used for the token, so trivially different spellings match."""
    value = value.strip()
    if field_type in {"name", "addr"}:
        return _SPACES.sub(" ", value).casefold()
    if field_type in {"email", "vpa"}:
        return value.casefold()
    if field_type == "pan":
        return value.upper()
    if field_type == "phone":
        digits = _NON_DIGITS.sub("", value)
        return digits[-10:] if len(digits) >= 10 else digits
    if field_type == "acct":
        return _SPACES.sub("", value).upper()
    return value


def compute_token(hmac_key: bytes, field_type: str, value: str) -> str:
    if field_type not in FIELD_TYPES:
        raise ValueError(f"unknown field type: {field_type}")
    message = f"{field_type}\x1f{normalise(field_type, value)}".encode()
    digest = hmac.new(hmac_key, message, hashlib.sha256).digest()
    return f"tok_{field_type}_{base64.urlsafe_b64encode(digest[:24]).decode()}"


def token_field_type(token: str) -> str:
    match = TOKEN_PATTERN.match(token)
    if not match or match.group(1) not in FIELD_TYPES:
        raise Invalid("not a valid token")
    return match.group(1)


@dataclass(frozen=True)
class Revealed:
    value: str
    field_type: str


class Tokeniser:
    def __init__(self, db: Database, keys: TenantKeys) -> None:
        self._db = db
        self._keys = keys

    async def tokenise_many(
        self, ctx: TenantContext, items: Sequence[tuple[str, str]]
    ) -> list[str]:
        """Tokenise (field_type, value) pairs and store their encrypted values."""
        if not items:
            return []
        hmac_key = await self._keys.get(ctx, "hmac")
        pii_key = await self._keys.get(ctx, "pii")
        tokens: list[str] = []
        rows: dict[str, dict[str, object]] = {}
        for field_type, value in items:
            token = compute_token(hmac_key.key, field_type, value)
            tokens.append(token)
            if token not in rows:
                aad = f"{ctx.tenant_id}|{token}".encode()
                rows[token] = {
                    "t": ctx.tenant_id,
                    "token": token,
                    "ft": field_type,
                    "c": encrypt(pii_key.key, value.strip().encode(), aad),
                    "kv": pii_key.version,
                }
        async with self._db.vault_tx(ctx) as conn:
            await conn.execute(
                text(
                    "INSERT INTO vault.pii_token (tenant_id, token, field_type, ciphertext, key_version) "
                    "VALUES (:t, :token, :ft, :c, :kv) ON CONFLICT (tenant_id, token) DO NOTHING"
                ),
                list(rows.values()),
            )
        return tokens

    async def tokenise(self, ctx: TenantContext, field_type: str, value: str) -> str:
        return (await self.tokenise_many(ctx, [(field_type, value)]))[0]

    async def token_for(self, ctx: TenantContext, field_type: str, value: str) -> str:
        """Compute a token for lookup only. Stores nothing."""
        hmac_key = await self._keys.get(ctx, "hmac")
        return compute_token(hmac_key.key, field_type, value)

    async def reveal(self, ctx: TenantContext, token: str, reason: str) -> Revealed:
        """Return the clear value behind a token.

        Allowed only if the field's catalogue entry lists the caller's purpose. The access is
        written to the audit log, and committed, before the value is returned.
        """
        field_type = token_field_type(token)
        async with self._db.tenant_tx(ctx) as conn:
            allowed = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM core.field_purpose WHERE field_type = :ft AND purpose_code = :p"
                    ),
                    {"ft": field_type, "p": ctx.purpose},
                )
            ).first()
            await record_audit(
                conn,
                ctx,
                action="pii.reveal" if allowed else "pii.reveal.denied",
                resource=f"token:{token}",
                details={"field_type": field_type, "reason": reason},
            )
        if not allowed:
            raise Forbidden(f"purpose {ctx.purpose} may not read {field_type} values")
        async with self._db.vault_tx(ctx) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT ciphertext, key_version FROM vault.pii_token WHERE token = :token"
                    ),
                    {"token": token},
                )
            ).first()
        if row is None:
            raise NotFound("token not found")
        key = await self._keys.get(ctx, "pii", row.key_version)
        aad = f"{ctx.tenant_id}|{token}".encode()
        return Revealed(decrypt(key.key, bytes(row.ciphertext), aad).decode(), field_type)
