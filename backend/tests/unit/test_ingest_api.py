import json
import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_api_key
from app.repositories.stores import StoreIdentity
from app.services.rate_limit import RateLimiter
from tests.conftest import TENANT_A, FakePublisher
from tests.unit.test_event_contract import make_event

API_KEY = "sf_test-key-for-unit-tests"
STORE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
IDENTITY = StoreIdentity(store_id=STORE_ID, tenant_id=TENANT_A, region="Europe")


class FakeStoreRepo:
    def __init__(self, session: object) -> None:
        pass

    async def resolve_api_key_hash(self, key_hash: str) -> StoreIdentity | None:
        return IDENTITY if key_hash == hash_api_key(API_KEY) else None


@pytest.fixture(autouse=True)
def _wire(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import deps

    monkeypatch.setattr(deps, "StoreRepository", FakeStoreRepo)
    # Freeze the clock so a second boundary cannot reset counters mid-test.
    monkeypatch.setattr(
        deps, "RateLimiter", lambda s, r, b: RateLimiter(s, r, b, clock=lambda: 5000.0)
    )
    client.app.dependency_overrides[deps.get_session] = lambda: object()  # type: ignore[attr-defined]


def post(client: TestClient, events: list[dict[str, Any]], key: str | None = API_KEY) -> Any:
    headers = {"X-API-Key": key} if key else {}
    return client.post("/api/v1/ingest/events", json={"events": events}, headers=headers)


def event(**overrides: Any) -> dict[str, Any]:
    return make_event(store_id=str(STORE_ID), **overrides)


def test_valid_batch_is_accepted_with_202_and_produced_with_headers(client: TestClient) -> None:
    publisher: FakePublisher = client.app.state.publisher  # type: ignore[attr-defined]
    response = post(client, [event() for _ in range(3)])
    assert response.status_code == 202
    assert response.json() == {"accepted": 3, "rejected": 0, "rejected_events": []}
    (batch,) = publisher.batches
    assert len(batch) == 3
    first = batch[0]
    assert first.key == str(STORE_ID)  # keyed by store for per-store ordering
    assert first.headers["tenant-id"] == str(TENANT_A)
    assert first.headers["schema-version"] == "1"
    assert first.headers["request-id"] == response.headers["X-Request-ID"]
    assert "produced-at" in first.headers
    assert json.loads(first.value)["items"][0]["unit_price"] == "19.99"


def test_batch_of_500_is_accepted(client: TestClient) -> None:
    response = post(client, [event() for _ in range(500)])
    assert response.status_code == 202
    assert response.json()["accepted"] == 500
    assert len(client.app.state.publisher.batches[0]) == 500  # type: ignore[attr-defined]


def test_more_than_500_events_is_rejected(client: TestClient) -> None:
    response = post(client, [event() for _ in range(501)])
    assert response.status_code == 422
    assert client.app.state.publisher.batches == []  # type: ignore[attr-defined]


def test_other_stores_events_are_rejected_per_event_and_not_produced(client: TestClient) -> None:
    other = make_event(store_id=str(uuid.uuid4()))
    response = post(client, [event(), other])
    body = response.json()
    assert response.status_code == 202
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert body["rejected_events"][0]["index"] == 1
    assert body["rejected_events"][0]["event_id"] == other["event_id"]
    assert "store_id" in body["rejected_events"][0]["error"]
    produced = client.app.state.publisher.batches[0]  # type: ignore[attr-defined]
    assert len(produced) == 1


def test_invalid_events_are_reported_and_not_produced(client: TestClient) -> None:
    bad = event(channel="fax")
    unsupported = event(event_version=2)
    response = post(client, [bad, unsupported])
    body = response.json()
    assert response.status_code == 202
    assert body["accepted"] == 0 and body["rejected"] == 2
    assert client.app.state.publisher.batches == []  # type: ignore[attr-defined]


def test_non_object_event_fails_envelope_validation(client: TestClient) -> None:
    response = post(client, ["nope"])  # type: ignore[list-item]
    assert response.status_code == 422


@pytest.mark.parametrize("key", [None, "sf_wrong"])
def test_missing_or_unknown_api_key_is_401(client: TestClient, key: str | None) -> None:
    assert post(client, [event()], key=key).status_code == 401


def test_oversized_body_is_413(client: TestClient) -> None:
    big = event(items=[{"product_id": str(uuid.uuid4()), "quantity": 1, "unit_price": "1.00"}])
    big["note"] = "x" * (260 * 1024)
    response = client.post(
        "/api/v1/ingest/events", json={"events": [big]}, headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 413


def test_malformed_envelope_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ingest/events", content=b"not json", headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 422


def test_kafka_down_returns_503_with_retry_after_and_app_survives(client: TestClient) -> None:
    client.app.state.publisher.fail = True  # type: ignore[attr-defined]
    response = post(client, [event()])
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    client.app.state.publisher.fail = False  # type: ignore[attr-defined]
    assert post(client, [event()]).status_code == 202


def test_rate_limit_returns_429_with_retry_after(client: TestClient) -> None:
    client.app.state.settings.ingest_rate_burst = 2  # type: ignore[attr-defined]
    responses = [post(client, [event()]) for _ in range(3)]
    assert [r.status_code for r in responses] == [202, 202, 429]
    assert int(responses[2].headers["Retry-After"]) >= 1


def test_secrets_never_appear_in_logs(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        post(client, [event()])
        post(client, [event()], key="sf_wrong-key-value")
    text = " ".join(r.getMessage() + str(r.__dict__) for r in caplog.records)
    assert API_KEY not in text
    assert "sf_wrong-key-value" not in text
