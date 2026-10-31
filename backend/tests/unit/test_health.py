import asyncio
from collections.abc import Iterator, Mapping

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_readiness_checks
from app.core.config import Settings
from app.main import create_app
from app.services.health import CheckFn, run_readiness


async def _ok() -> None:
    return None


def _failing(exc: Exception) -> CheckFn:
    async def check() -> None:
        raise exc

    return check


@pytest.fixture
def client_with_checks() -> Iterator[tuple[TestClient, dict[str, CheckFn]]]:
    app = create_app(Settings(health_check_timeout_seconds=0.1, realtime_enabled=False))
    checks: dict[str, CheckFn] = {"postgres": _ok, "redis": _ok, "kafka": _ok}

    def override() -> Mapping[str, CheckFn]:
        return checks

    app.dependency_overrides[get_readiness_checks] = override
    with TestClient(app) as client:
        yield client, checks


def test_liveness_returns_ok_without_touching_dependencies() -> None:
    # No override: if /health probed a real dependency this would fail or hang.
    with TestClient(create_app(Settings(realtime_enabled=False))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_200_when_all_dependencies_reachable(
    client_with_checks: tuple[TestClient, dict[str, CheckFn]],
) -> None:
    client, _ = client_with_checks
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["unreachable"] == []
    assert set(body["checks"]) == {"postgres", "redis", "kafka"}


def test_ready_returns_503_naming_the_unreachable_dependency(
    client_with_checks: tuple[TestClient, dict[str, CheckFn]],
) -> None:
    client, checks = client_with_checks
    checks["redis"] = _failing(ConnectionError("redis://:secret@redis:6379 refused"))
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["unreachable"] == ["redis"]
    assert body["checks"]["redis"] == {"status": "unreachable", "error": "ConnectionError"}
    assert body["checks"]["postgres"]["status"] == "ok"
    # Exception messages (which may contain credentials) must not be exposed.
    assert "secret" not in response.text


def test_ready_names_every_unreachable_dependency(
    client_with_checks: tuple[TestClient, dict[str, CheckFn]],
) -> None:
    client, checks = client_with_checks
    checks["postgres"] = _failing(OSError("down"))
    checks["kafka"] = _failing(RuntimeError("down"))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert sorted(response.json()["unreachable"]) == ["kafka", "postgres"]


def test_ready_treats_a_hanging_dependency_as_unreachable(
    client_with_checks: tuple[TestClient, dict[str, CheckFn]],
) -> None:
    client, checks = client_with_checks

    async def hang() -> None:
        await asyncio.sleep(30)

    checks["kafka"] = hang
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["kafka"] == {"status": "unreachable", "error": "TimeoutError"}


async def test_run_readiness_runs_checks_concurrently() -> None:
    async def slow() -> None:
        await asyncio.sleep(0.2)

    checks: dict[str, CheckFn] = {"a": slow, "b": slow, "c": slow}
    loop = asyncio.get_running_loop()
    started = loop.time()
    report = await run_readiness(checks, timeout_seconds=1.0)
    assert report.status == "ready"
    assert loop.time() - started < 0.5
