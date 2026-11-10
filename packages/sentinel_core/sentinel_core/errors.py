"""Errors that map to RFC 9457 problem responses."""

from dataclasses import dataclass, field


@dataclass
class FieldError:
    field: str
    message: str


@dataclass
class SentinelError(Exception):
    """Base error. `status` and `title` become the problem document."""

    detail: str
    status: int = 500
    title: str = "Internal error"
    errors: list[FieldError] = field(default_factory=list)

    def __str__(self) -> str:
        return self.detail


class Unauthenticated(SentinelError):
    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(detail, 401, "Unauthenticated")


class Forbidden(SentinelError):
    def __init__(self, detail: str = "Not permitted") -> None:
        super().__init__(detail, 403, "Forbidden")


class NotFound(SentinelError):
    def __init__(self, detail: str = "Not found") -> None:
        super().__init__(detail, 404, "Not found")


class Conflict(SentinelError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, 409, "Conflict")


class Invalid(SentinelError):
    def __init__(self, detail: str, errors: list[FieldError] | None = None) -> None:
        super().__init__(detail, 422, "Invalid request", errors or [])


class TooLarge(SentinelError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail, 413, "Payload too large")
