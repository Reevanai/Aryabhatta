# ADR 0011: Ports and adapters for every external system, with stubs

Status: Proposed (Phase 1, 2026-11-10)

## Context

Connectivity to RBI, RBIH, I4C, DoT, NPCI, CERSAI and FIU-IND systems needs bank-side agreements
that do not exist. Vendor capabilities (screening data, liveness, threat feeds) need licences.

## Decision

Each external system is a port owned by the consuming module, with a real adapter where one can
be built and a deterministic stub that returns synthetic data. Adapters declare their outbound
hosts, credentials reference, licence acknowledgement and rate limits. Adapters for specifications
that could not be verified are stubs that raise a clear `NotVerified` error on export paths.

## Alternatives considered

- **Call external APIs directly from domain code** — untestable and couples modules to unverified specifications.
- **A single integration service** — hides ownership; each module knows its own needs best.

## Consequences

- Demo and tests run entirely on stubs.
- Enabling a real adapter is a tenant configuration step with an audit record.
