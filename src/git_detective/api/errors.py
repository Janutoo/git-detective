"""Turn every failure into the same JSON error body.

Exceptions from the lower layers can contain absolute paths, so their messages
go to the server log and clients get a fixed message instead.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from git_detective.api.registry import RepositoryNotFoundError
from git_detective.api.runner import AnalysisBusyError, AnalysisTooLargeError
from git_detective.exceptions import GitDetectiveError, StorageError

logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 5

_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


class ApiError(Exception):
    """An expected failure with a message that is safe to show to clients."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: Sequence[Mapping[str, Any]] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details}},
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Only these fields: `input` would echo the request and `ctx` may hold
        # values that cannot be serialized.
        details = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return _error_response(
            422, "validation_error", "The request is not valid.", details=details
        )

    @app.exception_handler(HTTPException)
    def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        return _error_response(
            exc.status_code,
            _HTTP_ERROR_CODES.get(exc.status_code, "http_error"),
            str(exc.detail),
            headers=exc.headers,
        )

    @app.exception_handler(RepositoryNotFoundError)
    def handle_unknown_repository(
        _: Request, __: RepositoryNotFoundError
    ) -> JSONResponse:
        return _error_response(
            404,
            "repository_not_found",
            "This repository is not on the server's allowlist.",
        )

    @app.exception_handler(AnalysisBusyError)
    def handle_busy(_: Request, __: AnalysisBusyError) -> JSONResponse:
        return _error_response(
            429,
            "analysis_busy",
            "Too many analyses are running. Try again shortly.",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    @app.exception_handler(AnalysisTooLargeError)
    def handle_too_large(_: Request, __: AnalysisTooLargeError) -> JSONResponse:
        return _error_response(
            422,
            "analysis_too_large",
            "The result is too large to store. Use a smaller 'top' or 'limit'.",
        )

    @app.exception_handler(StorageError)
    def handle_storage_error(_: Request, exc: StorageError) -> JSONResponse:
        logger.error("Storage error: %s", exc)
        return _error_response(
            503, "storage_unavailable", "The analysis history is unavailable."
        )

    # Everything else the core raises is about the state of the repository.
    @app.exception_handler(GitDetectiveError)
    def handle_repository_error(_: Request, exc: GitDetectiveError) -> JSONResponse:
        logger.warning("Repository cannot be analyzed: %s", exc)
        return _error_response(
            409,
            "repository_unavailable",
            "The repository cannot be read. See the server log for details.",
        )

    # Starlette re-raises the exception after this, so the server still logs it.
    @app.exception_handler(Exception)
    def handle_unexpected(_: Request, __: Exception) -> JSONResponse:
        return _error_response(500, "internal_error", "Internal server error.")
