# Sentinel

Modular fraud, AML and cyber-risk platform for Indian co-operative banks, small banks and NBFCs.

Current state: **Phase 2 — platform core.** Tenancy, authentication and roles, the audit log,
per-tenant encryption, PII tokenisation and data ingestion work and are tested. Detection, case
management and the analyst console do not exist yet. See [`CHANGELOG.md`](CHANGELOG.md).

All data in this repository is synthetic.

## Run it

Needs Docker with Compose, `make`, and [`uv`](https://docs.astral.sh/uv/).

```bash
make up
```

```bash
make seed
```

```bash
make demo
```

```bash
make test
```

| Command | What it does |
|---------|--------------|
| `make up` | Builds the image and starts PostgreSQL, Redpanda, OpenBao, Keycloak, the API and the ingest worker. Applies migrations. |
| `make seed` | Creates the demo tenant and two branches, then loads `examples/*.csv` through the API. |
| `make demo` | Shows quarantined rows, a tokenised event from the bus, an audited PII reveal, a refused reveal, and audit-chain verification. |
| `make test` | Starts PostgreSQL, Redpanda and OpenBao, then runs all tests with coverage. |
| `make check` | Format, lint, strict types, module boundaries, licence allow-list. |
| `make down` | Stops everything and deletes its data. |

API documentation is served at `http://localhost:8000/v1/docs` once the stack is up.

Behind a TLS-inspecting proxy the image build needs the proxy and its CA; see the comment in
[`deploy/compose/Dockerfile`](deploy/compose/Dockerfile).

The credentials in `deploy/compose/dev.env` and the Keycloak realm file are public,
development-only values.

## Layout

| Path | Contents |
|------|----------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Design |
| [`docs/adr/`](docs/adr/README.md) | Decisions |
| [`docs/api/openapi.yaml`](docs/api/openapi.yaml) | REST contract (checked against the code by a test) |
| [`docs/events/`](docs/events/README.md) | Event contracts |
| [`packages/sentinel_core/`](packages/sentinel_core/README.md) | Tenancy, auth, audit, crypto, tokenisation, outbox |
| [`modules/ingestion/`](modules/ingestion/README.md) | Batch and stream intake, validation, quarantine |
| [`apps/api/`](apps/api/README.md), [`apps/ingest/`](apps/ingest/README.md) | Deployables |
| [`deploy/compose/`](deploy/compose/) | Development stack |
| [`research/`](research/README.md) | Phase 0 research and licence findings |
