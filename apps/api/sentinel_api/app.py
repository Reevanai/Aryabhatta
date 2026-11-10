"""API application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sentinel_core.auth import Authenticator, HttpJwksProvider, JwksProvider
from sentinel_core.config import get_settings
from sentinel_core.logging import configure_logging
from sentinel_core.runtime import Platform, build_platform
from sentinel_core.web import install_error_handlers
from sentinel_ingestion.public import BatchService, IngestionService
from sentinel_ingestion.public import router as ingestion_router

from sentinel_api.platform_routes import router as platform_router


def create_app(platform: Platform | None = None, jwks: JwksProvider | None = None) -> FastAPI:
    owns_platform = platform is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_platform:
            await app.state.platform.aclose()

    if platform is None:
        settings = get_settings()
        configure_logging(settings.log_level)
        platform = build_platform(settings)
    app = FastAPI(
        title="Sentinel API",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        redoc_url=None,
    )
    app.state.platform = platform
    app.state.authenticator = Authenticator(
        platform.db, jwks or HttpJwksProvider(), platform.settings.oidc_audience
    )
    ingestion = IngestionService(platform)
    app.state.ingestion = ingestion
    app.state.batches = BatchService(platform, ingestion)
    install_error_handlers(app)
    app.include_router(platform_router, prefix="/v1")
    app.include_router(ingestion_router, prefix="/v1")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
