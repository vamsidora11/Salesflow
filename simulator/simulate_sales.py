"""Realistic sales event generator for demos and load tests.

Reads credentials produced by `backend/scripts/seed_demo_data.py` (seed-output/seed_output.json) and posts
batched events to /api/v1/ingest/events at a configurable rate, with a daily traffic pattern
(quiet at night, lunch and evening peaks) and a skewed product mix.

    python simulate_sales.py --rate 200 --tenant "Acme Retail" --duration 120
"""

import argparse
import json
import math
import random
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

BATCH_MAX = 500
CHANNELS = ["pos", "online", "mobile"]
CHANNEL_WEIGHTS = [0.6, 0.3, 0.1]


def daily_pattern(hour: float) -> float:
    """Relative traffic multiplier (0.1 .. 1.0): lunch peak ~12:30, evening peak ~18:30."""

    def bump(centre: float, width: float) -> float:
        return math.exp(-(((hour - centre) / width) ** 2))

    return 0.1 + 0.9 * max(bump(12.5, 2.5), bump(18.5, 3.0) * 0.9)


def product_weights(count: int) -> list[float]:
    """Zipf-like skew: a few best sellers, a long tail."""
    return [1 / (i + 1) ** 0.9 for i in range(count)]


@dataclass
class Store:
    id: str
    api_key: str
    name: str


@dataclass
class Stats:
    sent: int = 0
    accepted: int = 0
    rejected: int = 0
    errors: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **counts: int) -> None:
        with self.lock:
            for key, value in counts.items():
                setattr(self, key, getattr(self, key) + value)


def make_event(store_id: str, products: list[dict[str, Any]], weights: list[float], rng: random.Random, when: datetime) -> dict[str, Any]:
    chosen = {p["id"]: p for p in rng.choices(products, weights=weights, k=rng.choice([1, 1, 2, 3]))}
    items = []
    for product in chosen.values():
        price = (Decimal(product["price"]) * Decimal(str(rng.choice([1, 1, 1, 0.9])))).quantize(Decimal("0.01"))
        items.append({"product_id": product["id"], "quantity": rng.choice([1, 1, 1, 2, 3]), "unit_price": str(price)})
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "sale.completed",
        "event_version": 1,
        "event_time": when.isoformat().replace("+00:00", "Z"),
        "store_id": store_id,
        "channel": rng.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0],
        "currency": "USD",
        "items": items,
    }


def post_batch(base_url: str, store: Store, events: list[dict[str, Any]], stats: Stats) -> None:
    request = urllib.request.Request(
        f"{base_url}/api/v1/ingest/events",
        data=json.dumps({"events": events}).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": store.api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
        stats.add(sent=len(events), accepted=body["accepted"], rejected=body["rejected"])
    except urllib.error.HTTPError as exc:
        stats.add(sent=len(events), errors=len(events))
        if exc.code in (429, 503):
            time.sleep(float(exc.headers.get("Retry-After", "1")))  # honour backpressure
    except OSError:
        stats.add(sent=len(events), errors=len(events))
        time.sleep(1)


def run(args: argparse.Namespace) -> int:
    creds = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    tenant = next((t for t in creds["tenants"] if t["name"] == args.tenant), creds["tenants"][0])
    stores = [Store(s["id"], s["api_key"], s["name"]) for s in tenant["stores"]][: args.stores or None]
    products = tenant["products"]
    weights = product_weights(len(products))
    rng = random.Random(args.seed)
    stats = Stats()
    started = time.monotonic()
    next_report = started + 5
    print(f"simulating {len(stores)} stores of {tenant['name']} at ~{args.rate} events/s")

    interval = 0.25  # build and send one slice every 250 ms
    try:
        while args.duration == 0 or time.monotonic() - started < args.duration:
            tick = time.monotonic()
            now = datetime.now(UTC)
            hour = now.hour + now.minute / 60
            factor = daily_pattern(hour) if args.daily_pattern else 1.0
            target = args.rate * interval * factor
            count = int(target) + (1 if rng.random() < target - int(target) else 0)
            by_store: dict[str, list[dict[str, Any]]] = {}
            for _ in range(count):
                store = rng.choice(stores)
                by_store.setdefault(store.id, []).append(make_event(store.id, products, weights, rng, now))
            threads = []
            for store in stores:
                events = by_store.get(store.id, [])
                for i in range(0, len(events), BATCH_MAX):
                    t = threading.Thread(target=post_batch, args=(args.url, store, events[i : i + BATCH_MAX], stats))
                    t.start()
                    threads.append(t)
            for t in threads:
                t.join()
            if time.monotonic() >= next_report:
                print(f"sent={stats.sent} accepted={stats.accepted} rejected={stats.rejected} errors={stats.errors}")
                next_report += 5
            time.sleep(max(0.0, interval - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        pass
    print(f"done: sent={stats.sent} accepted={stats.accepted} rejected={stats.rejected} errors={stats.errors}")
    return 0 if stats.errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL (nginx or API)")
    parser.add_argument("--credentials", default="seed-output/seed_output.json")
    parser.add_argument("--tenant", default="Acme Retail")
    parser.add_argument("--rate", type=float, default=20.0, help="Events per second (daily pattern applied)")
    parser.add_argument("--stores", type=int, default=0, help="Use only the first N stores (0 = all)")
    parser.add_argument("--duration", type=float, default=0, help="Seconds to run (0 = until Ctrl+C)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-daily-pattern", dest="daily_pattern", action="store_false")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
