"""Alembic script-directory helpers shared by the worker and tests."""

from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import PROJECT_ROOT


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    if database_url:
        # configparser interpolation: escape '%'
        cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def head_revisions() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())
