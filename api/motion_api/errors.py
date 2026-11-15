"""Typed application errors mapped to HTTP responses in main.py."""
from __future__ import annotations


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class Unauthenticated(AppError):
    status = 401
    code = "unauthenticated"


class Forbidden(AppError):
    """Authorization denial. Always audited (outcome='denied')."""

    status = 403
    code = "forbidden"

    def __init__(self, message: str, *, action: str = "access", target_type: str | None = None,
                 target_id: str | None = None, branch_id: int | None = None, details: dict | None = None):
        super().__init__(message, details=details)
        self.action = action
        self.target_type = target_type
        self.target_id = target_id
        self.branch_id = branch_id


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"


class Invalid(AppError):
    status = 422
    code = "invalid"


class RateLimited(AppError):
    status = 429
    code = "rate_limited"
