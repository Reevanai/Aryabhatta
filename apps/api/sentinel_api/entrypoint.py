"""Module-level ASGI app for hosts that import `app` instead of calling a factory.

If the required configuration is present this is the full API. If it is not (for example a
hosted preview with no database or identity provider attached) it serves the health check and
the API documentation, and answers every `/v1` call with a 503 problem document that names
what is missing. It never starts half-configured.
"""

from fastapi import Depends, FastAPI
from pydantic import ValidationError
from sentinel_core.errors import SentinelError
from sentinel_core.web import install_error_handlers
from sentinel_ingestion.public import router as ingestion_router
from sentinel_txn_fraud.public import router as txn_fraud_router
from sentinel_txn_fraud.public import score_router

from sentinel_api.app import create_app
from sentinel_api.platform_routes import router as platform_router


class NotConfigured(SentinelError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "This deployment has no backing services configured. Missing: " + ", ".join(missing),
            503,
            "Service not configured",
        )


def _missing(exc: Exception) -> list[str]:
    if isinstance(exc, ValidationError):
        return sorted("SENTINEL_" + str(err["loc"][0]).upper() for err in exc.errors())
    return [str(exc)]


def unconfigured_app(missing: list[str]) -> FastAPI:
    async def refuse() -> None:
        raise NotConfigured(missing)

    app = FastAPI(
        title="Sentinel API (not configured)",
        version="0.2.0",
        description="Documentation only. No backing services are attached to this deployment.",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.include_router(platform_router, prefix="/v1", dependencies=[Depends(refuse)])
    app.include_router(ingestion_router, prefix="/v1", dependencies=[Depends(refuse)])
    app.include_router(txn_fraud_router, prefix="/v1", dependencies=[Depends(refuse)])
    app.include_router(score_router, prefix="/v1", dependencies=[Depends(refuse)])

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, object]:
        return {"status": "not_configured", "missing": missing}

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, object]:
        return {
            "service": "Sentinel API",
            "status": "not_configured",
            "missing": missing,
            "documentation": "/v1/docs",
        }

    return app


def build() -> FastAPI:
    try:
        return create_app()
    except (ValidationError, RuntimeError) as exc:
        return unconfigured_app(_missing(exc))


app = build()
