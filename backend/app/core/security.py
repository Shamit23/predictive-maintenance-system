"""Shared-secret guards for state-changing endpoints.

  * `X-API-Key`      (API_KEY)         - sensor ingestion and the trusted simulation worker
  * `X-Control-Key`  (CONTROL_API_KEY) - operator actions: start / stop / change speed of a simulation run

The two keys are different secrets with different powers: the control key cannot ingest or act as a worker, and the
API key cannot start or stop simulations. Keys are compared in constant time and never logged or returned.
These are shared secrets for a trusted environment, not user authentication.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.services.errors import AppError

_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_control_header = APIKeyHeader(name="X-Control-Key", auto_error=False)


def _matches(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and secrets.compare_digest(supplied.encode(), expected.encode())


def require_api_key(request: Request, supplied: str | None = Depends(_header)) -> None:
    if not _matches(supplied, request.app.state.settings.api_key.get_secret_value()):
        raise AppError("unauthorized", 401, "Missing or invalid API key.")


def require_control_key(request: Request, supplied: str | None = Depends(_control_header)) -> None:
    expected = request.app.state.settings.control_api_key
    if expected is None:  # fail closed: operator controls need an explicitly configured key
        raise AppError("control_disabled", 503,
                       "Operator controls are disabled: CONTROL_API_KEY is not configured on the server.")
    if not _matches(supplied, expected.get_secret_value()):
        raise AppError("unauthorized", 401, "Missing or invalid control key.")
