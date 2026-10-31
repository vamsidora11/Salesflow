"""End-to-end latency probe: ingest one event, then poll a WebSocket until its update arrives.

Run while a load test is going (or idle) to measure ingest -> dashboard latency:

    python tests/load/latency_probe.py --url http://localhost:8080 --email analyst@acme.example \
        --password <from seed output> --samples 50

Needs the `websockets` package (pip install websockets). Prints p50/p95/max in seconds.
"""

import argparse
import asyncio
import json
import statistics
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def run(args: argparse.Namespace) -> int:
    import websockets

    creds = json.loads(await asyncio.to_thread(Path(args.credentials).read_text, "utf-8"))
    tenant = next(t for t in creds["tenants"] if t["name"] == args.tenant)
    store, product = tenant["stores"][0], tenant["products"][0]
    token = _post(
        f"{args.url}/api/v1/auth/login", {"email": args.email, "password": tenant["password"]}, {}
    )["access_token"]

    ws_url = args.url.replace("http", "ws", 1) + "/api/v1/ws/dashboard"
    latencies: list[float] = []
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))  # token never in the URL
        assert json.loads(await ws.recv())["type"] == "auth.ok"
        await ws.send(json.dumps({"type": "subscribe", "store_id": store["id"]}))
        for _ in range(args.samples):
            event = {
                "event_id": str(uuid.uuid4()),
                "event_type": "sale.completed",
                "event_version": 1,
                "event_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "store_id": store["id"],
                "channel": "pos",
                "currency": "USD",
                "items": [
                    {"product_id": product["id"], "quantity": 1, "unit_price": product["price"]}
                ],
            }
            started = time.monotonic()
            _post(
                f"{args.url}/api/v1/ingest/events",
                {"events": [event]},
                {"X-API-Key": store["api_key"]},
            )
            deadline = started + 10
            while time.monotonic() < deadline:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if message.get("type") == "update" and message.get("store_id") == store["id"]:
                    latencies.append(time.monotonic() - started)
                    break
            await asyncio.sleep(0.5)

    latencies.sort()
    if not latencies:
        print("no updates received")
        return 1
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    print(
        f"samples={len(latencies)} p50={statistics.median(latencies):.2f}s p95={p95:.2f}s "
        f"max={latencies[-1]:.2f}s  (target: p95 < 3s)"
    )
    return 0 if p95 < 3 else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--credentials", default="seed-output/seed_output.json")
    p.add_argument("--tenant", default="Acme Retail")
    p.add_argument("--email", default="analyst@acme.example")
    p.add_argument("--samples", type=int, default=30)
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
