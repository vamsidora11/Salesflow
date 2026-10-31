"""Locust load test: ingestion throughput and (optionally) dashboard query latency.

    # needs credentials from the seed script: seed-output/seed_output.json
    cd backend
    locust -f tests/load/locustfile.py --headless -u 40 -r 10 -t 5m --host http://localhost:8080

Each simulated user is one store posting batches of events with its own API key. Tune
BATCH_SIZE and wait_time so that (users x batch / wait) reaches the target of 2,000 events/s.
The pass criteria are in plan section 15 (no HTTP 5xx; p95 end-to-end latency < 3 s, measured with
tests/load/latency_probe.py; consumer lag back to ~0 within 30 s of stopping).
"""

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from locust import HttpUser, constant_pacing, events, task

CREDENTIALS = Path(os.environ.get("SEED_FILE", "../seed-output/seed_output.json"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))
_creds = json.loads(CREDENTIALS.read_text(encoding="utf-8")) if CREDENTIALS.exists() else None
_stores: list[tuple[dict[str, Any], list[dict[str, Any]]]] = (
    [(s, t["products"]) for t in _creds["tenants"] for s in t["stores"]] if _creds else []
)
_next = 0


@events.test_start.add_listener
def _check(environment: Any, **_: Any) -> None:
    if not _stores:
        raise SystemExit(f"No credentials at {CREDENTIALS}; run the seed script first.")


def _event(store_id: str, products: list[dict[str, Any]]) -> dict[str, Any]:
    product = products[hash(uuid.uuid4()) % len(products)]
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "sale.completed",
        "event_version": 1,
        "event_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "store_id": store_id,
        "channel": "pos",
        "currency": "USD",
        "items": [
            {
                "product_id": product["id"],
                "quantity": 1,
                "unit_price": str(Decimal(product["price"]).quantize(Decimal("0.01"))),
            }
        ],
    }


class StoreUser(HttpUser):
    # One batch every 0.25 s per user => 4 x BATCH_SIZE events/s per user.
    wait_time = constant_pacing(0.25)

    def on_start(self) -> None:
        global _next
        self.store, self.products = _stores[_next % len(_stores)]
        _next += 1
        self.headers = {"X-API-Key": self.store["api_key"], "Content-Type": "application/json"}

    @task
    def ingest(self) -> None:
        body = {"events": [_event(self.store["id"], self.products) for _ in range(BATCH_SIZE)]}
        with self.client.post(
            "/api/v1/ingest/events", json=body, headers=self.headers, catch_response=True
        ) as response:
            if response.status_code == 202:
                response.success()
            elif response.status_code == 429:
                response.failure("rate limited (raise INGEST_RATE_* or add stores)")
            else:
                response.failure(f"HTTP {response.status_code}")
