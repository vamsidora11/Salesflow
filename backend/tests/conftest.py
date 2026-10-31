import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import create_access_token
from app.main import create_app
from app.streaming.producer import KafkaUnavailableError, OutboundMessage

TEST_SECRET = "test-secret-not-for-production"
TENANT_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
TENANT_B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


class FakeRedis:
    """Just enough Redis for rate limiting, sequences and freshness."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.data[name] = int(self.data.get(name, 0)) + 1
        return int(self.data[name])

    async def expire(self, name: str, time: int) -> bool:
        self.expiries[name] = time
        return True

    async def get(self, name: str) -> Any:
        return self.data.get(name)

    async def set(self, name: str, value: Any, ex: int | None = None) -> bool:
        self.data[name] = value
        return True

    async def aclose(self) -> None:
        return None


class FakePublisher:
    def __init__(self) -> None:
        self.batches: list[list[OutboundMessage]] = []
        self.fail = False

    async def publish(self, messages: list[OutboundMessage]) -> None:
        if self.fail:
            raise KafkaUnavailableError("down")
        self.batches.append(messages)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        realtime_enabled=False,
        jwt_secret=TEST_SECRET,
        health_check_timeout_seconds=0.1,
        ws_auth_timeout_seconds=0.4,
    )


def token_for(
    role: str = "analyst",
    tenant_id: uuid.UUID = TENANT_A,
    user_id: uuid.UUID | None = None,
    expires_minutes: int = 5,
    secret: str = TEST_SECRET,
) -> str:
    return create_access_token(
        secret=secret,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
        expires_minutes=expires_minutes,
    )


def auth_headers(role: str = "analyst", tenant_id: uuid.UUID = TENANT_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(role, tenant_id)}"}


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    application.state.redis = FakeRedis()
    application.state.publisher = FakePublisher()
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
