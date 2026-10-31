import asyncio
import time
import uuid
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.realtime.websocket_manager import ConnectionManager
from tests.conftest import TENANT_A, TENANT_B, TEST_SECRET, token_for
from tests.realtime.test_manager import update

URL = "/api/v1/ws/dashboard"


def manager_of(client: TestClient) -> ConnectionManager:
    return client.app.state.ws_manager  # type: ignore[no-any-return, attr-defined]


def broadcast(client: TestClient, tenant: Any, message: dict[str, Any]) -> None:
    assert client.portal is not None
    client.portal.call(manager_of(client).broadcast, str(tenant), message)


def settle(client: TestClient, seconds: float = 0.2) -> None:
    assert client.portal is not None
    client.portal.call(asyncio.sleep, seconds)


def connect_authed(client: TestClient, tenant: Any = TENANT_A, **claims: Any) -> Any:
    ws = client.websocket_connect(URL)
    session = ws.__enter__()
    session.send_json({"type": "auth", "token": token_for(tenant_id=tenant, **claims)})
    assert session.receive_json()["type"] == "auth.ok"
    return ws, session


def test_connection_without_auth_is_closed_after_the_timeout(client: TestClient) -> None:
    started = time.monotonic()
    with client.websocket_connect(URL) as ws, pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    assert exc.value.code == 4401
    assert 0.3 <= time.monotonic() - started < 3  # ws_auth_timeout_seconds=0.4 in tests


def test_invalid_token_is_rejected(client: TestClient) -> None:
    with client.websocket_connect(URL) as ws:
        ws.send_json({"type": "auth", "token": "not-a-jwt"})
        assert ws.receive_json() == {"type": "auth.error"}
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401


def test_expired_token_is_rejected(client: TestClient) -> None:
    with client.websocket_connect(URL) as ws:
        ws.send_json({"type": "auth", "token": token_for(expires_minutes=-1)})
        assert ws.receive_json()["type"] == "auth.error"


def test_first_message_must_be_auth(client: TestClient) -> None:
    with client.websocket_connect(URL) as ws:
        ws.send_json({"type": "subscribe", "store_id": "x"})
        assert ws.receive_json()["type"] == "auth.error"


def test_valid_auth_ping_pong_and_registration(client: TestClient) -> None:
    ctx, ws = connect_authed(client)
    try:
        assert manager_of(client).connection_count(str(TENANT_A)) == 1
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["type"] == "error"
    finally:
        ctx.__exit__(None, None, None)
    settle(client)
    assert manager_of(client).connection_count() == 0  # cleaned up on disconnect


def test_update_is_pushed_to_authenticated_client(client: TestClient) -> None:
    ctx, ws = connect_authed(client)
    try:
        broadcast(client, TENANT_A, update(5))
        message = ws.receive_json()
        assert message["type"] == "update" and message["seq"] == 5
    finally:
        ctx.__exit__(None, None, None)


def test_tenant_a_never_receives_tenant_b_updates(client: TestClient) -> None:
    ctx_a, ws_a = connect_authed(client, TENANT_A)
    ctx_b, ws_b = connect_authed(client, TENANT_B)
    try:
        broadcast(client, TENANT_B, update(1, store="b-store"))
        broadcast(client, TENANT_A, update(2, store="a-store"))
        settle(client)
        ws_a.send_json({"type": "ping"})
        ws_b.send_json({"type": "ping"})
        a_messages = [ws_a.receive_json(), ws_a.receive_json()]
        b_messages = [ws_b.receive_json(), ws_b.receive_json()]
        assert [m.get("store_id") for m in a_messages if m["type"] == "update"] == ["a-store"]
        assert [m.get("store_id") for m in b_messages if m["type"] == "update"] == ["b-store"]
    finally:
        ctx_a.__exit__(None, None, None)
        ctx_b.__exit__(None, None, None)


def test_subscription_filter_is_respected(client: TestClient) -> None:
    ctx, ws = connect_authed(client)
    try:
        ws.send_json({"type": "subscribe", "store_id": "wanted"})
        assert ws.receive_json()["type"] == "subscribed"
        broadcast(client, TENANT_A, update(1, store="other"))
        broadcast(client, TENANT_A, update(2, store="wanted"))
        got = ws.receive_json()
        assert got["store_id"] == "wanted"
        ws.send_json({"type": "unsubscribe", "store_id": "wanted"})
        assert ws.receive_json()["type"] == "unsubscribed"
    finally:
        ctx.__exit__(None, None, None)


def test_bad_subscribe_payload_is_an_error_not_a_crash(client: TestClient) -> None:
    ctx, ws = connect_authed(client)
    try:
        ws.send_json({"type": "subscribe", "store_id": 123})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    finally:
        ctx.__exit__(None, None, None)


def test_connection_is_closed_when_the_token_expires(client: TestClient) -> None:
    claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(TENANT_A),
        "role": "analyst",
        "exp": time.time() + 1.0,
    }
    token = jwt.encode(claims, TEST_SECRET, algorithm="HS256")
    started = time.monotonic()
    with client.websocket_connect(URL) as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json()["type"] == "auth.ok"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401
    assert time.monotonic() - started < 5
