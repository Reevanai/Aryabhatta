# Changelog

## Unreleased

### Phase 2 — platform core (2026-11-10)
Built and tested:
- Tenancy (bank → branch) with forced row-level security on every tenant table; branch scope.
- Keycloak OIDC authentication, role-based permissions, demo realm with OTP required for users.
- Append-only, hash-chained audit log with signed anchors and a verification endpoint.
- Per-tenant keys (OpenBao transit or local), envelope encryption, PII tokenisation with a
  separate vault schema and purpose-checked, audited reveal.
- Ingestion: batch CSV, HTTP and Kafka intake; validation; encrypted quarantine; idempotent
  transactions; transactional outbox and relay.
- Development stack, Makefile, GitHub Actions CI, licence allow-list check.

Changed from the Phase 1 design:
- Row validation uses Pydantic, not Pandera.
- Added `ingestion.transaction_ref` for idempotency (a partitioned table cannot enforce it).
- The outbox is ordered by an identity column.
- Batch datasets are parties, accounts and transactions; loans and devices move to Phase 3.

Added after the phase: a module-level app entrypoint (`app.py`) with an unconfigured mode, so
hosts such as Vercel can build and serve the API documentation without backing services.

Measured: 194 tests, 93% coverage; batch ingestion about 1,470 rows/s on the development stack.

### Phase 1 — architecture (2026-11-10)
- Added `ARCHITECTURE.md` (8 Mermaid diagrams, data model, tenancy, security).
- Added 16 ADRs under `docs/adr/`, three of which change the baseline stack.
- Added `docs/api/openapi.yaml` (40 operations: 27 designed, 13 provisional).
- Added event envelope and five payload schemas under `docs/events/`.
- No feature code.

### Phase 0 — research (2026-11-10)
- Added `research/RESEARCH.md`, `research/LICENCES.md`.
- Added `research/capability_matrix_provisional.csv` (72 rows from the brief).
- `research/capability_matrix.csv` is header-only pending `capabilities.txt`.
