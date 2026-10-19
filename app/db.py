"""Database engine/session helpers. Processes stay stateless: the engine is a
connection pool only; all durable state lives in Postgres (PRD §2)."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def engine_for(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def get_engine() -> Engine:
    return engine_for(get_settings().database_url)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    with factory() as session:
        yield session


def ping(engine: Engine) -> None:
    """Raise if the database is unreachable."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def current_revision(engine: Engine) -> str | None:
    """Alembic revision stored in the DB, or None if migrations never ran."""
    with engine.connect() as conn:
        try:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        except ProgrammingError:  # alembic_version table does not exist yet
            return None
    return row[0] if row else None
