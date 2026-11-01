"""API-key protection for state-changing endpoints (ingestion, alert acknowledgement).

Clients send the key in the `X-API-Key` header. It is compared in constant time and never logged.
This is a shared-secret guard for a trusted environment, not user authentication.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.services.errors import AppError

_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(request: Request, supplied: str | None = Depends(_header)) -> None:
    expected = request.app.state.settings.api_key.get_secret_value()
    if not supplied or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise AppError("unauthorized", 401, "Missing or invalid API key.")
