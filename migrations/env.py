"""Alembic environment. URL precedence: explicit `sqlalchemy.url` set by the caller
(tests) > DATABASE_URL from app.config (environment, then .env)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
            # Carry-over (a): app_readonly's password comes from DATABASE_READONLY_URL,
            # re-applied on every upgrade (idempotent; never logged).
            if config.attributes.get("sync_readonly_password", True):
                from app.db_roles import sync_readonly_password

                sync_readonly_password(connection, get_settings().database_readonly_url)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
