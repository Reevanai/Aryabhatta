"""The scorer: one endpoint, sized and released separately from the rest of the API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sentinel_core.auth import Authenticator, HttpJwksProvider, JwksProvider
from sentinel_core.config import get_settings
from sentinel_core.logging import configure_logging
from sentinel_core.runtime import Platform, build_platform
from sentinel_core.web import install_error_handlers
from sentinel_txn_fraud.public import ScoringService, StateStore, build_state, score_router


def create_app(
    platform: Platform | None = None,
    jwks: JwksProvider | None = None,
    state: StateStore | None = None,
) -> FastAPI:
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
        title="Sentinel scorer",
        version="0.4.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.platform = platform
    app.state.authenticator = Authenticator(
        platform.db, jwks or HttpJwksProvider(), platform.settings.oidc_audience
    )
    app.state.scoring = ScoringService(platform, state or build_state(platform.settings))
    install_error_handlers(app)
    app.include_router(score_router, prefix="/v1")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
