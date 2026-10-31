from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

SessionFactoryT = async_sessionmaker[AsyncSession]


def to_async_url(database_url: str) -> str:
    """Accept the plain postgresql:// URL used by asyncpg and Alembic alike."""
    if database_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    return database_url


def make_engine(database_url: str, *, null_pool: bool = False) -> AsyncEngine:
    """`null_pool=True` for short-lived event loops (Celery tasks, scripts)."""
    if null_pool:
        return create_async_engine(to_async_url(database_url), poolclass=NullPool)
    return create_async_engine(to_async_url(database_url), pool_size=10, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One transaction: commit on success, roll back on error."""
    async with factory() as session, session.begin():
        yield session
