"""Optional API-key authentication.

Set ``RELEASEGUARD_API_KEY`` to require an ``X-API-Key`` header on protected
routes. When it is unset the service stays open, which keeps local development
and the free demo simple. The key is read on every request, so tests and
deployments can change it without a restart.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status

API_KEY_ENV = "RELEASEGUARD_API_KEY"


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency: reject the request unless the API key matches.

    ``hmac.compare_digest`` compares in constant time, so response timing does
    not reveal how many leading characters of a guessed key were right.
    """
    expected = os.environ.get(API_KEY_ENV)
    if not expected:
        return  # auth not configured
    if x_api_key is None or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
