"""alembic environment configuration (async engine)"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base
import app.db.models  # noqa: F401  (register metadata)

config = context.config
# Must be the asyncpg URL: env.py builds an AsyncEngine, and Render hands us a
# plain ``postgresql://`` connection string. Reuse the app's normaliser so the
# CLI path (``alembic upgrade head`` in scripts/start.sh) works too.
config.set_main_option("sqlalchemy.url", settings.ASYNC_DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # ``app.main`` injects its long-lived async engine through
    # ``config.attributes["connection"]`` so migrations reuse the app pool
    # instead of opening a second one.
    connectable = config.attributes.get("connection")
    if connectable is not None:
        async with connectable.connect() as connection:
            await connection.run_sync(_do_run_migrations)
        return

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
