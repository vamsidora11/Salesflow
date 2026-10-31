import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import make_engine

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    return os.environ.get("DATABASE_URL") or get_settings().database_url


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = make_engine(_database_url(), null_pool=True)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported (Timescale DDL needs a live database).")
run_migrations_online()
