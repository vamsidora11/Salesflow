"""WebSocket session logic for /api/v1/ws/dashboard (protocol: docs/websocket.md)."""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import Settings
from app.core.security import InvalidTokenError, decode_access_token
from app.realtime.websocket_manager import ClientStuckError, Connection, ConnectionManager

logger = logging.getLogger(__name__)

CLOSE_UNAUTHORIZED = 4401
CLOSE_SLOW_CLIENT = 4408


class SeqSource(Protocol):
    async def current(self, tenant_id: str) -> int: ...


async def _authenticate(websocket: WebSocket, settings: Settings) -> tuple[str, float] | None:
    """First message must be {"type":"auth","token":...} within the timeout."""
    try:
        first = await asyncio.wait_for(
            websocket.receive_json(), timeout=settings.ws_auth_timeout_seconds
        )
        if not isinstance(first, dict) or first.get("type") != "auth":
            raise ValueError("first message must be auth")
        claims = decode_access_token(str(first.get("token", "")), settings.jwt_secret)
        tenant_id = str(uuid.UUID(claims["tenant_id"]))
        return tenant_id, float(claims["exp"])
    except (TimeoutError, WebSocketDisconnect):
        return None
    except (InvalidTokenError, KeyError, ValueError, json.JSONDecodeError):
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "auth.error"})
        return None


async def run_dashboard_session(
    websocket: WebSocket,
    manager: ConnectionManager,
    settings: Settings,
    seq_source: SeqSource,
) -> None:
    await websocket.accept()
    auth = await _authenticate(websocket, settings)
    if auth is None:
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    tenant_id, token_exp = auth

    conn = Connection(
        websocket, tenant_id, settings.ws_max_pending_updates, settings.ws_send_timeout_seconds
    )
    try:
        seq = await seq_source.current(tenant_id)
    except Exception:
        seq = 0
    await websocket.send_json({"type": "auth.ok", "seq": seq})

    manager.add(conn)
    writer = asyncio.create_task(conn.run_writer(settings.ws_heartbeat_seconds))
    try:
        await _read_loop(websocket, conn, writer, token_exp)
    except WebSocketDisconnect:
        pass
    finally:
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError, ClientStuckError, Exception):
            await writer
        manager.remove(conn)


async def _read_loop(
    websocket: WebSocket, conn: Connection, writer: "asyncio.Task[None]", token_exp: float
) -> None:
    while True:
        remaining = token_exp - time.time()
        if remaining <= 0:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        receive = asyncio.ensure_future(websocket.receive_json())
        done, _ = await asyncio.wait(
            {receive, writer}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if writer in done:
            receive.cancel()
            if isinstance(writer.exception(), ClientStuckError):
                await websocket.close(code=CLOSE_SLOW_CLIENT)
            return
        if receive not in done:
            receive.cancel()
            continue  # token expiry is re-checked at the top of the loop
        try:
            message: Any = receive.result()
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "detail": "invalid JSON"})
            continue
        await _handle_client_message(websocket, conn, message)


async def _handle_client_message(websocket: WebSocket, conn: Connection, message: Any) -> None:
    kind = message.get("type") if isinstance(message, dict) else None
    if kind == "ping":
        await websocket.send_json({"type": "pong"})
    elif kind in ("subscribe", "unsubscribe"):
        store_id, region = message.get("store_id"), message.get("region")
        if not isinstance(store_id, str | None) or not isinstance(region, str | None):
            await websocket.send_json(
                {"type": "error", "detail": "store_id/region must be strings"}
            )
            return
        if kind == "subscribe":
            conn.subscribe(store_id, region)
        else:
            conn.unsubscribe(store_id, region)
        await websocket.send_json({"type": f"{kind}d", "store_id": store_id, "region": region})
    else:
        await websocket.send_json({"type": "error", "detail": "unknown message type"})
