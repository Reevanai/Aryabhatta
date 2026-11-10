# ADR 0007: Deterministic per-tenant tokenisation with a separate PII vault

Status: Proposed (Phase 1, 2026-11-10)

## Context

DPDP privacy by design, and the practical need to keep personal data out of events, logs,
features and caches. Detection needs equality joins on identifiers; entity resolution needs fuzzy
comparison.

## Decision

At ingestion, replace direct identifiers with tokens computed as a keyed HMAC per tenant and field
type. Store the clear value encrypted in a `vault` schema reachable only by the tokenisation
service's database role. Produce non-reversible comparison keys (normalised phonetic and n-gram
hashes) for entity resolution. Every personal-data column is registered in a data catalogue with a
classification and purpose tags; reading a clear value requires a declared purpose that the column
allows, and is audited.

## Alternatives considered

- **Random tokens** — no equality joins without a lookup on every event.
- **Format-preserving encryption** — reversible by anyone holding the key, and harder to scope per purpose.
- **Field-level encryption only** — ciphertext leaks into every downstream store and cannot be joined.

## Consequences

- Deterministic tokens reveal equality within a tenant; that is intended and limited to one tenant by the key.
- Low-entropy fields can be brute-forced by someone holding the tenant HMAC key, so that key is as sensitive as the encryption key and lives in the key service.
- Fuzzy matching quality on comparison keys is lower than on clear text; measured in Phase 6.
