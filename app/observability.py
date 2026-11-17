"""Request IDs, structured logs, a body-size limit and Prometheus metrics.

* Every request gets an ID (taken from ``X-Request-ID`` if it looks safe,
  otherwise generated) that appears in the response header, the error body and
  the log line, so one failing call can be traced end to end.
* Each request writes one JSON log line: method, path, status, duration.
* Bodies larger than ``RELEASEGUARD_MAX_BODY_BYTES`` (default 2 MB) are
  rejected with 413 before they are read.
* ``GET /metrics`` exposes request counts, latency and analyses per risk level
  in the Prometheus text format. The ``path`` label is the route template
  (``/analyze``), never the raw URL, so random URLs cannot create unbounded
  label values.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.errors import error_response

log = logging.getLogger("releaseguard")

REQUESTS = Counter("releaseguard_requests_total", "HTTP requests handled", ["method", "path", "status"])
LATENCY = Histogram("releaseguard_request_duration_seconds", "HTTP request latency in seconds", ["method", "path"])
ANALYSES = Counter("releaseguard_analyses_total", "Risk analyses produced, by level", ["level"])

DEFAULT_MAX_BODY_BYTES = 2_000_000
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_LOG_FIELDS = ("request_id", "method", "path", "status", "duration_ms")


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, easy for log tools to parse."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                   "level": record.levelname, "message": record.getMessage()}
        payload.update({k: getattr(record, k) for k in _LOG_FIELDS if hasattr(record, k)})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def max_body_bytes() -> int:
    """Read on each request so the limit can be changed without a restart."""
    try:
        return int(os.environ.get("RELEASEGUARD_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES))
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "")
    return supplied if _SAFE_REQUEST_ID.match(supplied) else uuid.uuid4().hex


def install_observability(app: FastAPI) -> None:
    configure_logging()

    @app.middleware("http")
    async def observe(request: Request, call_next):
        request.state.request_id = _request_id(request)
        started = time.perf_counter()
        status = 500  # stays 500 if an exception escapes the handler
        try:
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_body_bytes():
                response = error_response(request, 413, "payload_too_large",
                                          f"Request body is larger than {max_body_bytes()} bytes.")
            else:
                response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            REQUESTS.labels(request.method, path, str(status)).inc()
            LATENCY.labels(request.method, path).observe(elapsed)
            log.info("request", extra={"request_id": request.state.request_id, "method": request.method,
                                       "path": request.url.path, "status": status,
                                       "duration_ms": round(elapsed * 1000, 2)})

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
