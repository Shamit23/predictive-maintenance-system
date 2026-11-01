"""Uniform error envelope: {"error": {"code", "message", "details"}}.

Clients never see tracebacks, SQL, filesystem paths, settings or request echoes; those go to the server log.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import AppError
from app.services.ingestion import record_ingestion_error

log = logging.getLogger("app.api")


def _envelope(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}},
                        status_code=status, headers=headers)


def _is_ingest(request: Request) -> bool:
    return request.method == "POST" and request.url.path == "/api/v1/readings"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        headers = {"WWW-Authenticate": "ApiKey"} if exc.status == 401 else None
        return _envelope(exc.status, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Report where and why, but never echo the submitted values back.
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]), "message": e["msg"],
                    "type": e["type"]} for e in exc.errors()]
        if _is_ingest(request):
            record_ingestion_error(
                request.app.state.session_factory, code="validation_error",
                message="; ".join(f"{d['field']}: {d['message']}" for d in details), payload=exc.body)
        return _envelope(422, "validation_error", "Request validation failed.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return _envelope(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(OperationalError)
    async def _db_down(request: Request, exc: OperationalError):
        log.error("database unavailable", extra={"ctx": {"path": request.url.path}})
        return _envelope(503, "database_unavailable", "The database is temporarily unavailable.")

    @app.exception_handler(DBAPIError)
    async def _db_error(request: Request, exc: DBAPIError):
        log.exception("database error", extra={"ctx": {"path": request.url.path}})
        return _envelope(500, "internal_error", "An internal error occurred.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"ctx": {"path": request.url.path}})
        return _envelope(500, "internal_error", "An internal error occurred.")
