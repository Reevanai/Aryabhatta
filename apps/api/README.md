# api

The FastAPI application. Mounts the platform routes and each module's router under `/v1`.

```bash
uvicorn sentinel_api.app:create_app --factory --port 8000
```

Interactive documentation: `/v1/docs`. Health check: `/healthz`.

Every `/v1` route needs a bearer token from the tenant's Keycloak realm. The tenant comes from
the token; it is never a request parameter. Errors are RFC 9457 problem documents and never echo
submitted values.

## Hosts that import `app`

`sentinel_api.entrypoint:app` (re-exported by the root `app.py`) is a module-level app for
platforms such as Vercel that cannot call a factory. Without `SENTINEL_DATABASE_URL_APP` and the
other required settings it runs **unconfigured**: `/healthz` and `/v1/docs` work, and every `/v1`
call returns a 503 problem document naming what is missing.

A serverless host runs only this API. Batch files stay queued and events stay in the outbox unless
the ingest worker runs somewhere, and uploads need a writable `SENTINEL_FILESTORE_DIR`.

Limits: no rate limiting, no request metrics or traces yet.
