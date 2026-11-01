"""Alembic environment. Run from `backend/`:  alembic upgrade head

URL precedence: config.attributes["url"] (tests) > DATABASE_URL from env/.env.
The URL is never written to logs or alembic.ini.
"""
from alembic import context
from sqlalchemy import create_engine, pool

from app.db.base import Base
import app.models  # noqa: F401  (registers tables on Base.metadata)

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.attributes.get("url")
    if url:
        return url
    from app.core.config import get_settings

    return get_settings().database_url.get_secret_value()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a live database.")
run_migrations_online()
