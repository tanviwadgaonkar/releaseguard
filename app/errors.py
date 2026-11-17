"""One consistent JSON error shape for every failure.

Every error response looks like::

    {"error": {"code": "validation_error", "message": "...",
               "request_id": "...", "details": [...]}}

so a client only has to handle one format. Validation details name the field
and the problem but never echo the submitted value back, and unexpected errors
return a generic message (the traceback goes to the log, not to the caller).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("releaseguard")

_CODES = {
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    422: "validation_error",
}


def error_response(request: Request, status_code: int, code: str, message: str,
                   details: list[dict] | None = None, headers: dict | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    error: dict = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        error["details"] = details
    headers = dict(headers or {})
    if request_id:
        headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=status_code, content={"error": error}, headers=headers)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"field": ".".join(str(part) for part in err["loc"] if part != "body"),
                "message": err["msg"]} for err in exc.errors()]
    return error_response(request, 422, "validation_error", "Request failed validation.", details)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _CODES.get(exc.status_code, "http_error")
    return error_response(request, exc.status_code, code, str(exc.detail),
                          headers=getattr(exc, "headers", None))


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled error", exc_info=exc,
              extra={"request_id": getattr(request.state, "request_id", None),
                     "method": request.method, "path": request.url.path})
    return error_response(request, 500, "internal_error",
                          "Something went wrong. Quote the request_id when reporting it.")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
