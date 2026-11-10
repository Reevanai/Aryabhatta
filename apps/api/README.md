# api

The FastAPI application. Mounts the platform routes and each module's router under `/v1`.

```bash
uvicorn sentinel_api.app:create_app --factory --port 8000
```

Interactive documentation: `/v1/docs`. Health check: `/healthz`.

Every `/v1` route needs a bearer token from the tenant's Keycloak realm. The tenant comes from
the token; it is never a request parameter. Errors are RFC 9457 problem documents and never echo
submitted values.

Limits: no rate limiting, no request metrics or traces yet.
