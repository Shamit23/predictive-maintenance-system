"""FastAPI application factory.

Run (from backend/, venv active, after `alembic upgrade head`):
    uvicorn app.main:create_app --factory --reload

Startup is fail-fast: if the database is unreachable or the active model cannot be determined/validated
(missing, ambiguous, invalid), the process exits with a clear message instead of serving a broken API.
The model is loaded once here, not per request.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, make_session_factory
from app.services.model_registry import load_active_model

log = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_db_engine(settings.database_url.get_secret_value())
        app.state.db_engine = engine
        app.state.session_factory = make_session_factory(engine)
        try:
            with app.state.session_factory() as session:
                app.state.model = load_active_model(session, settings)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Predictive Maintenance API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.model = None
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "X-API-Key", "X-Control-Key", "X-Simulation-Run"],
    )
    install_error_handlers(app)
    app.include_router(router)
    return app
