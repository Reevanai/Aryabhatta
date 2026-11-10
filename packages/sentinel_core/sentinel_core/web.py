"""Shared FastAPI pieces: authentication dependency and RFC 9457 error responses."""

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from sentinel_core.auth import Authenticator, Principal
from sentinel_core.errors import SentinelError, Unauthenticated
from sentinel_core.logging import get_logger
from sentinel_core.runtime import Platform

log = get_logger(__name__)
_bearer = HTTPBearer(auto_error=False)
PROBLEM = "application/problem+json"


def get_platform(request: Request) -> Platform:
    platform: Platform = request.app.state.platform
    return platform


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise Unauthenticated()
    authenticator: Authenticator = request.app.state.authenticator
    return await authenticator.authenticate(credentials.credentials)


PlatformDep = Annotated[Platform, Depends(get_platform)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _problem(status: int, title: str, detail: str, **extra: Any) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, **extra}
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(body, status_code=status, media_type=PROBLEM, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SentinelError)
    async def _sentinel(_: Request, exc: SentinelError) -> JSONResponse:
        extra: dict[str, Any] = {}
        if exc.errors:
            extra["errors"] = [{"field": e.field, "message": e.message} for e in exc.errors]
        return _problem(exc.status, exc.title, exc.detail, **extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo the submitted values: they may be personal data.
        errors = [
            {
                "field": ".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]),
                "message": str(err["msg"]).removeprefix("Value error, "),
            }
            for err in exc.errors()
        ]
        return _problem(422, "Invalid request", "request failed validation", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, str(exc.detail), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return _problem(500, "Internal error", "an unexpected error occurred")
