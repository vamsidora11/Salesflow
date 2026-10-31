from fastapi import APIRouter, WebSocket

from app.core.config import Settings
from app.realtime.sequence import RedisSequenceSource
from app.realtime.session import run_dashboard_session
from app.realtime.websocket_manager import ConnectionManager

# WebSockets are not part of OpenAPI; see docs/websocket.md.
router = APIRouter(prefix="/ws", tags=["realtime"])


@router.websocket("/dashboard")
async def dashboard(websocket: WebSocket) -> None:
    state = websocket.app.state
    settings: Settings = state.settings
    manager: ConnectionManager = state.ws_manager
    await run_dashboard_session(websocket, manager, settings, RedisSequenceSource(state.redis))
