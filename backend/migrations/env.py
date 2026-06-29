"""Async-aware Alembic env.

Wired to :mod:`app.config` for the database URL and :mod:`app.models` for
``target_metadata`` so that ``alembic revision --autogenerate`` sees every
model registered on :class:`app.db.Base`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import app modules. ``app.models`` re-exports every model so that every
# table is registered on Base.metadata before autogenerate runs.
from app import models  # noqa: F401 — imported for side effect of registering tables
from app.config import settings
from app.db import Base

# Alembic Config object, providing access to the values within the .ini file.
config = context.config

# Inject the runtime DB URL from app settings so we have a single source of
# truth (env var DATABASE_URL).
config.set_main_option("sqlalchemy.url", settings.database_url)

# Set up loggers from the .ini file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations against it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
