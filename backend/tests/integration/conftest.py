"""Fixtures for tests that need real infrastructure. All skip cleanly when Docker is missing."""

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import make_engine, make_session_factory

BACKEND_DIR = Path(__file__).resolve().parents[2]
TIMESCALE_IMAGE = "timescale/timescaledb:latest-pg16"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        import docker  # type: ignore[import-not-found,unused-ignore]

        docker.from_env().ping()
    except Exception:
        return False
    return True


requires_docker = pytest.mark.skipif(not docker_available(), reason="Docker is not available")


def alembic_config(database_url: str) -> Config:
    os.environ["DATABASE_URL"] = database_url
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def _start_timescale() -> Any:
    from testcontainers.postgres import PostgresContainer

    return PostgresContainer(TIMESCALE_IMAGE, username="test", password="test", dbname="test")


def _plain_url(container: Any) -> str:
    return f"postgresql://test:test@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}/test"


@pytest.fixture(scope="session")
def timescale_url() -> Iterator[str]:
    if not docker_available():
        pytest.skip("Docker is not available")
    with _start_timescale() as container:
        url = _plain_url(container)
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture
def fresh_timescale_url() -> Iterator[str]:
    """A separate, un-migrated database for migration up/down tests."""
    if not docker_available():
        pytest.skip("Docker is not available")
    with _start_timescale() as container:
        yield _plain_url(container)


@pytest.fixture
async def db_engine(timescale_url: str) -> AsyncIterator[AsyncEngine]:
    engine = make_engine(timescale_url, null_pool=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(db_engine)


@pytest.fixture
async def pg(timescale_url: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(timescale_url)
    yield conn
    await conn.close()
