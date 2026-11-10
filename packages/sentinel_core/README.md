# sentinel_core

Platform services every module uses.

| Module | What it does |
|--------|--------------|
| `db` | The only place that opens database connections. Sets the tenant (and branch scope) on every transaction; row-level security reads it. Separate pools for the application, vault and relay roles. |
| `context` | `TenantContext`: tenant, actor, purpose, branch scope. |
| `auth` | Verifies OIDC bearer tokens against the tenant's Keycloak realm; maps realm roles to permissions. |
| `audit` | Append-only, hash-chained audit log with signed anchors and verification. |
| `crypto`, `tenant_keys` | Per-tenant key-encryption key in a key service (OpenBao transit, or a local one for tests); wrapped data keys per purpose; AES-256-GCM. |
| `tokenisation` | Deterministic per-tenant tokens for personal data; encrypted values in the `vault` schema; purpose-checked, audited reveal. |
| `events`, `outbox` | Event envelope with schema validation; transactional outbox and relay to Kafka. |
| `filestore` | Encrypted storage for uploaded files. |
| `logging` | JSON logs with a scrubber that removes identifier-like values. |
| `web` | FastAPI authentication dependency and RFC 9457 error responses. |
| `provisioning`, `cli` | Tenant creation and the `sentinel-admin` command. |
| `migrations` | Alembic migrations, run as the owner role. |

## Run

```bash
sentinel-admin migrate
```

```bash
sentinel-admin create-tenant --name "Bank" --issuer https://idp/realms/bank --jwks-uri https://idp/realms/bank/protocol/openid-connect/certs
```

```bash
sentinel-admin audit-anchor --tenant <tenant-id>
```

Configuration is read from `SENTINEL_*` environment variables (`config.py`).

## Database roles

| Role | Used by | Can reach |
|------|---------|-----------|
| `sentinel_owner` | Migrations, provisioning | Owns everything; still bound by row-level security |
| `sentinel_app` | API, workers | `core`, `ingestion`; no access to `vault` |
| `sentinel_vault` | Tokenisation only | `vault` and the field catalogue |
| `sentinel_relay` | Outbox relay | The outbox, all tenants |

## Limits

- **Local key service is for development and tests.** It derives tenant keys from one master key in
  configuration. Use OpenBao or the bank's KMS in production.
- **OpenBao authentication uses a static token.** AppRole or Kubernetes auth is deployment work.
- **Key rotation is not implemented.** Key versions are stored and honoured on read, but there is no
  command to add a version or re-wrap.
- **Audit anchors are stored in the same database.** They detect a rewritten chain only if the
  attacker cannot also forge the anchor signature (needs the tenant audit key). Exporting anchors
  to write-once storage is not built.
- **Requests refused for a missing role are not written to the audit log.** Refused PII reveals
  (wrong purpose) are.
- **Deterministic tokens reveal equality inside a tenant by design.** Low-entropy fields could be
  brute-forced by someone holding the tenant's HMAC key, so that key is as sensitive as the
  encryption key.
- **Retention policies have a table but no enforcement job** (Phase 12).
- **`Idempotency-Key` is not implemented.** Batch upload is idempotent by file hash, and
  transactions by source reference.
- **No OpenTelemetry traces or metrics yet**, only structured logs.
- **File store is a local directory**, encrypted and decrypted in memory, with a size cap.
