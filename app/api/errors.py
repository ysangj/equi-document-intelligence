"""C2 error envelope for every endpoint (API Contract -> Conventions -> C2).

`{"error": {"code", "message", "details": [{"loc", "message"}] | null}}`

- `ApiError` is raised by handlers for the documented 4xx cases.
- FastAPI's own errors (unknown route 404, 405, request validation 422,
  HTTPException) and unexpected exceptions are converted by `install_error_handlers`.
- `GET /api/health` returns its T1 body itself and never goes through these handlers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.api")

VALIDATION_MESSAGE = "Invalid request parameters."

# Model-level validator messages (loc is just "query"/"body") -> the input they are about.
_LOC_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sort|cannot sort by"), "sort"),
    (re.compile(r"date_from"), "date_from"),
    (re.compile(r"value or value_pct"), "value"),
    (re.compile(r"'action'"), "action"),  # discriminator: unknown or missing action
)
# Union/discriminator tags pydantic inserts into body locs ("AcceptAction", "function-after[...]").
_TAG_RE = re.compile(r"^(function-\w+\[.*\]|[A-Z]\w*Action|accept|edit|choose|confirm_alias|reject_alias|make_canonical)$")


class ApiError(Exception):
    """A documented error response (C2)."""

    def __init__(self, status: int, code: str, message: str,
                 details: Sequence[dict[str, Any]] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = list(details) if details else None


def envelope(code: str, message: str, details: Sequence[dict[str, Any]] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": list(details) if details else None}}


def error_response(status: int, code: str, message: str,
                   details: Sequence[dict[str, Any]] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope(code, message, details))


def not_found(message: str) -> ApiError:
    return ApiError(404, "not_found", message)


def validation_error(loc: str | None, message: str) -> ApiError:
    return ApiError(422, "validation_error", VALIDATION_MESSAGE, [{"loc": loc, "message": message}])


def invalid_action(message: str, loc: str | None = None, detail: str | None = None) -> ApiError:
    return ApiError(422, "invalid_action", message, [{"loc": loc, "message": detail or message}])


def _clean_msg(msg: str) -> str:
    return msg.removeprefix("Value error, ").removeprefix("Assertion failed, ")


def format_loc(loc: Sequence[Any], msg: str = "") -> str | None:
    """("query", "sort") -> "query.sort"; drops union tags and list indexes."""
    parts = [str(p) for p in loc if not isinstance(p, int) and not _TAG_RE.match(str(p))]
    if not parts:
        return None
    if len(parts) == 1 and parts[0] in ("query", "body", "path"):
        for pattern, name in _LOC_HINTS:
            if pattern.search(msg):
                parts.append(name)
                break
    return ".".join(parts)


def details_from_errors(errors: Sequence[dict[str, Any]], prefix: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for e in errors:
        msg = _clean_msg(str(e.get("msg", "invalid value")))
        loc = tuple(e.get("loc", ()))
        if prefix:
            loc = (prefix, *loc)
        item = {"loc": format_loc(loc, msg), "message": msg}
        key = (item["loc"], item["message"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def from_pydantic(err: ValidationError, prefix: str) -> ApiError:
    return ApiError(422, "validation_error", VALIDATION_MESSAGE, details_from_errors(err.errors(), prefix))


# Generic HTTPException status -> a C2 code (only documented codes are ever emitted).
_STATUS_CODES = {404: "not_found", 405: "method_not_allowed", 503: "db_unavailable"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        return error_response(422, "validation_error", VALIDATION_MESSAGE, details_from_errors(exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            message = "Not found." if exc.detail in (None, "Not Found") else str(exc.detail)
            return error_response(404, "not_found", message)
        if exc.status_code == 405:
            return error_response(405, "method_not_allowed",
                                  f"Method {request.method} is not allowed for {request.url.path}.")
        code = _STATUS_CODES.get(exc.status_code, "internal_error" if exc.status_code >= 500 else "validation_error")
        if code == "internal_error":
            return error_response(exc.status_code, code, "Internal server error.")
        resp = error_response(exc.status_code, code, str(exc.detail))
        for k, v in (exc.headers or {}).items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(ResponseValidationError)
    async def _response_validation(request: Request, exc: ResponseValidationError):
        log.error("response failed validation", extra={"event": "response_validation_error",
                                                       "path": request.url.path, "errors": len(exc.errors())})
        return error_response(500, "internal_error", "Internal server error.")

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError):
        log.warning("database unavailable", extra={"event": "db_unavailable", "path": request.url.path,
                                                   "error_type": type(exc.orig).__name__})
        return error_response(503, "db_unavailable", "The database is unavailable. Try again shortly.")

    @app.exception_handler(DBAPIError)
    async def _db_error(request: Request, exc: DBAPIError):
        if exc.connection_invalidated:
            return error_response(503, "db_unavailable", "The database is unavailable. Try again shortly.")
        log.error("database error", extra={"event": "db_error", "path": request.url.path,
                                           "error_type": type(exc.orig).__name__})
        return error_response(500, "internal_error", "Internal server error.")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.error("unhandled error", extra={"event": "internal_error", "path": request.url.path,
                                            "error_type": type(exc).__name__})
        return error_response(500, "internal_error", "Internal server error.")
