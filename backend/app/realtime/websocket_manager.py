"""Per-tenant WebSocket connections with filters, coalescing, and slow-client resync."""

import asyncio
import logging
from decimal import Decimal
from typing import Any, Protocol

from app.core.metrics import WS_CONNECTIONS

logger = logging.getLogger(__name__)


class Sendable(Protocol):
    async def send_json(self, data: Any) -> None: ...


class ClientStuckError(Exception):
    """A send did not complete in time; the socket should be closed."""


class Connection:
    def __init__(
        self,
        ws: Sendable,
        tenant_id: str,
        max_pending: int,
        send_timeout_seconds: float,
    ) -> None:
        self.ws = ws
        self.tenant_id = tenant_id
        self._max_pending = max_pending
        self._send_timeout = send_timeout_seconds
        self.store_ids: set[str] = set()
        self.regions: set[str] = set()
        # Latest value per (bucket, store): updates for the same bucket are coalesced.
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._resync = False
        self._wake = asyncio.Event()

    # --- filters ---------------------------------------------------------------------------

    def subscribe(self, store_id: str | None, region: str | None) -> None:
        if store_id:
            self.store_ids.add(store_id)
        if region:
            self.regions.add(region)

    def unsubscribe(self, store_id: str | None, region: str | None) -> None:
        if store_id:
            self.store_ids.discard(store_id)
        if region:
            self.regions.discard(region)

    def _matches(self, message: dict[str, Any]) -> bool:
        if not self.store_ids and not self.regions:
            return True
        return message.get("store_id") in self.store_ids or message.get("region") in self.regions

    # --- inbound updates -------------------------------------------------------------------

    def offer(self, message: dict[str, Any]) -> None:
        if message.get("type") != "update" or not self._matches(message) or self._resync:
            return
        key = (str(message.get("bucket")), str(message.get("store_id")))
        existing = self._pending.get(key)
        if existing is not None:
            message = _coalesce(existing, message)
        elif len(self._pending) >= self._max_pending:
            # Too far behind: drop the backlog and tell the client to reload.
            self._pending.clear()
            self._resync = True
            self._wake.set()
            return
        self._pending[key] = message
        self._wake.set()

    def pending_count(self) -> int:
        return len(self._pending)

    # --- outbound --------------------------------------------------------------------------

    async def _send(self, data: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(self.ws.send_json(data), timeout=self._send_timeout)
        except TimeoutError as exc:
            raise ClientStuckError from exc

    async def run_writer(self, heartbeat_seconds: float) -> None:
        """Flush pending updates, resync notices and heartbeats until cancelled."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                await self._send({"type": "heartbeat"})
                continue
            self._wake.clear()
            if self._resync:
                self._resync = False
                self._pending.clear()
                await self._send({"type": "resync_required"})
                continue
            batch = sorted(self._pending.values(), key=lambda m: int(m.get("seq", 0)))
            self._pending.clear()
            for message in batch:
                await self._send(message)


def _coalesce(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Keep the latest totals but add the deltas so the client does not lose increments."""
    merged = dict(new)
    merged["delta_revenue"] = str(
        Decimal(str(old.get("delta_revenue", "0"))) + Decimal(str(new.get("delta_revenue", "0")))
    )
    merged["delta_units"] = int(old.get("delta_units", 0)) + int(new.get("delta_units", 0))
    return merged


class ConnectionManager:
    def __init__(self) -> None:
        self._by_tenant: dict[str, set[Connection]] = {}

    def add(self, conn: Connection) -> None:
        self._by_tenant.setdefault(conn.tenant_id, set()).add(conn)
        WS_CONNECTIONS.inc()

    def remove(self, conn: Connection) -> None:
        conns = self._by_tenant.get(conn.tenant_id)
        if conns and conn in conns:
            conns.discard(conn)
            WS_CONNECTIONS.dec()
            if not conns:
                del self._by_tenant[conn.tenant_id]

    def connection_count(self, tenant_id: str | None = None) -> int:
        if tenant_id is not None:
            return len(self._by_tenant.get(tenant_id, ()))
        return sum(len(c) for c in self._by_tenant.values())

    async def broadcast(self, tenant_id: str, message: dict[str, Any]) -> None:
        """Deliver only to connections of `tenant_id`; never across tenants."""
        for conn in list(self._by_tenant.get(tenant_id, ())):
            conn.offer(message)
