# ADR 0005: OpenBao and SOPS; envelope encryption with per-tenant keys

Status: Proposed (Phase 1, 2026-11-10)

Baseline change: Vault → OpenBao (same API).

## Context

The baseline names Vault or SOPS. Vault 1.15 and later is under BUSL 1.1 (licence file read
2026-11-10), which restricts hosted or embedded competitive offerings. Per-tenant encryption keys are
a platform requirement.

## Decision

Use OpenBao (MPL-2.0), which keeps the Vault HTTP API, for runtime secrets and its transit engine
for key operations. Use SOPS for secrets that live in git. Each tenant has a key-encryption key in
transit; data keys are generated per tenant and purpose, stored wrapped beside the data, and cached
briefly in memory. Key rotation re-wraps data keys. Destroying a tenant's key-encryption key is the
cryptographic erasure step at offboarding. The key service is a port, so a bank can supply its own
Vault or HSM-backed KMS.

## Alternatives considered

- **HashiCorp Vault** — licence risk for a hosted offering; still usable by a bank through the same API.
- **SOPS only** — no runtime key operations, rotation or per-tenant key lifecycle.
- **Cloud KMS** — many target banks run on premises.

## Consequences

- OpenBao becomes a critical dependency for reading PII; it needs HA and tested recovery.
- Losing a tenant key loses that tenant's PII; backup and recovery procedures are mandatory before go-live.
