# SalesFlow

Real-time sales analytics: sales events are ingested over HTTP, streamed through Kafka, stored in
TimescaleDB, aggregated continuously and pushed to a React dashboard over WebSockets. Scheduled jobs
produce reports and reconcile totals; users can export CSV (streamed) or Excel (async).

Specification: [SALES_ANALYTICS_PLAN.md](SALES_ANALYTICS_PLAN.md) · Progress and open items: [PROGRESS.md](PROGRESS.md)
· [Architecture](docs/architecture.md) · [WebSocket protocol](docs/websocket.md) · [Deployment](docs/deployment.md)
· [Performance](docs/performance.md) · API docs: `http://localhost:8080/api/docs` (committed in [docs/openapi.json](docs/openapi.json)).

> **Verification status.** The unit/API/realtime/streaming-logic tests and the frontend tests run without
> Docker and pass. Everything that needs real Kafka/TimescaleDB/Redis/MinIO (Compose stack, migrations on a real
> database, integration tests, load tests) has been written but **not run yet**. See PROGRESS.md.

## Architecture

```
  Sales sources (POS, simulator)
        | POST /api/v1/ingest/events  (X-API-Key)
        v
  Ingestion API (FastAPI) -- validate, rate-limit --> Kafka  sales.events.v1 (key=store_id, 6 partitions)
                                                         |   sales.events.dlq.v1 (3 partitions)
                                                         v  consumer group "sales-writer"
                                               Stream consumer  --(1) insert, ON CONFLICT DO NOTHING--> TimescaleDB
                                                         |  (2) commit Kafka offsets                     sales_events (hypertable)
                                                         |  (3) publish live update                      sales_hourly / sales_daily (continuous aggregates)
                                                         v
                                                  Redis pub/sub  analytics:{tenant}
                                                         v
                                         API instances --> WebSocket /api/v1/ws/dashboard --> React dashboard
  React dashboard --REST (JWT)--> analytics / exports / stores / products
  Celery worker + Beat: reconcile_daily_totals, generate_daily_report, cleanup_expired_exports,
                        refresh_dead_letter_summary, run_export  --> MinIO / S3
```

Three process types from one codebase: `api` (REST + WebSocket), `consumer` (`python -m app.streaming.consumer`),
`worker` + `beat` (Celery). Layering: routers -> services -> repositories (the only SQL); Kafka/Redis/S3 are adapters
called from services.

## Quick start

```bash
cp .env.example .env                 # replace every CHANGE_ME with a random value
docker compose up -d --build         # core stack; migrations run automatically
docker compose run --rm seed         # demo tenants, stores, 50 products each, users, API keys
docker compose --profile demo up -d simulator
open http://localhost:8080           # log in with a user from seed-output/seed_output.json
```

Optional: `docker compose --profile monitoring up -d` (Prometheus :9090, Grafana :3000).

### Developing without Docker for the unit tests

```bash
cd backend && python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy app scripts migrations && pytest   # integration tests auto-skip without Docker
cd ../frontend && npm ci && npm test && npm run build
```

## Delivery guarantees

* **Ingestion returns 202 = "in the durable log", not "in the database".** The producer is idempotent with
  `acks=all`; if Kafka is unreachable the API answers `503` + `Retry-After` and never buffers unboundedly
  (bounded librdkafka queue, undelivered messages purged).
* **At-least-once into the database, effectively once.** The consumer commits Kafka offsets only after the
  database transaction commits. A crash re-delivers the batch; the `INSERT ... ON CONFLICT (event_time, event_id,
  product_id) DO NOTHING` makes that harmless. Producers choose `event_id`, so their retries are deduplicated too.
* **Poison messages cannot block a partition.** Invalid messages (bad JSON/schema/version/header) go to the DLQ
  with `error-reason` headers. A batch that fails with a data error is retried per message; after N attempts the
  message is dead-lettered. Database outages (connection errors) pause and retry the *same* batch and are never dead-lettered.
* **Live updates are best-effort; the database is the source of truth.** Redis publish failures are logged and
  ignored. Clients refetch via REST after reconnect or `resync_required`. Slow clients get coalesced updates and,
  past a bound, a `resync_required` instead of an unbounded backlog.
* **Why aggregates serve the dashboard:** continuous aggregates are refreshed every minute and read in real-time
  mode, so queries touch pre-summed rows instead of scanning raw events, regardless of raw data volume.

## Design decisions

* One Kafka message per transaction, one table row per line item (`sales_events`, composite key including
  `event_time` because Timescale requires the partition column in every unique key). Repeated products inside one
  event are rejected so no line item is silently dropped.
* Money: strings in JSON, `Decimal` in Python, `NUMERIC` in SQL; the consumer recomputes `amount = quantity x unit_price`
  rather than trusting the producer. Floats are rejected at the boundary.
* Keying by `store_id` keeps each store's events ordered within a partition.
* Tenant isolation: tenant comes from the JWT/API key only; every repository query filters on it; another
  tenant's export job is a 404; WebSocket delivery is scoped by the Redis channel's tenant.
* WebSocket auth is first-message JWT (tokens never in URLs/logs), 5-second timeout, closes on token expiry.
* Retention: raw events compress after 7 days and drop after 90 (configurable); hourly aggregate compresses after 30 days
  and is kept 2 years; daily is kept forever. Raw events are the expensive part; aggregates are cheap and answer most questions.

## Defaults used for open decisions (plan section 17)

| Decision | Default chosen |
|---|---|
| Event broker | Kafka (KRaft, single node locally) |
| Kafka client | confluent-kafka |
| Batch orchestration | Celery Beat |
| Charts | Recharts |
| Excel library | XlsxWriter |
| Object storage | MinIO locally, S3 in production |
| WebSocket auth | First-message JWT (token never in the URL) |
| Currency handling | Single currency per tenant (`tenants.currency`, returned in every analytics `meta`) |
| Deployment | Single VM with Compose |

## Deviations from the plan (and why)

* **`channel` added to the continuous aggregates' grouping**, so `/analytics/by-channel` can read an aggregate as the plan requires.
* **`transactions` = `sum(txn)`, not `count(*)`.** `count(*)` over a per-line-item table counts line items, which would make
  average order value wrong. The consumer sets `txn = 1` on exactly one row per event.
* **`sales_events.request_id` column** so a request ID can be traced from the ingest log line to the consumer log
  line to the database row (plan section 16, phase 8).
* **`tenants.currency` column** and **`export_jobs.status = 'expired'`** (plan section 4/8 mention marking jobs expired but omit the status).
* **`materialized_only = false`** on the aggregates (real-time aggregation is off by default in TimescaleDB 2.13+).
* **Daily aggregate is hierarchical** (rolls up `sales_hourly`), as the plan suggests; its refresh window is 7 days.
* **Redis pattern subscription** (`analytics:*`) instead of per-tenant subscribe/unsubscribe; delivery is still tenant-scoped.
* **Live updates carry `delta_revenue` / `delta_units`** in addition to new totals so the client can adjust aggregated charts without a refetch; a 60 s reconciling refetch corrects drift.
* **Daily report is JSON only** (plan allows JSON plus PDF/XLSX); the XLSX exporter already exists if needed.
* **Consumer module path** is `app.streaming.consumer` (as in CLAUDE.md), not `app.workers.consumer` (plan section 3).
* The plan's "TaskFlow" conventions were not available; the OpenAPI conventions of plan section 10 are used (summaries, tags,
  `<tag>_<function>` operation IDs, documented error responses, examples, committed `openapi.json` + CI drift check).
* Users are created by the seed script only (no registration endpoint), and passwords use stdlib scrypt rather than bcrypt.

## 2-minute demo script

1. `docker compose up -d --build && docker compose run --rm seed` (show `docker compose ps`: all healthy).
2. `docker compose --profile demo up -d simulator`. Open http://localhost:8080 and log in as the analyst
   (`seed-output/seed_output.json`). **Say:** "Events go API -> Kafka -> consumer -> TimescaleDB; this chart updates over a WebSocket."
3. Point at the live indicator ("connected, updated 1s ago") and the revenue chart ticking without a refresh.
4. Filter by region/store; the KPI cards, charts and subscriptions change.
5. `docker compose stop api` -> indicator shows **reconnecting**; `docker compose start api` -> it reconnects and resyncs.
6. Resilience: `docker compose kill consumer` for 20 s, then `docker compose start consumer`; lag spikes then returns to 0 (Grafana: `--profile monitoring`); no gaps, no duplicates.
7. Show the DLQ: post one malformed event directly to Kafka, then
   `kafka-console-consumer ... --topic sales.events.dlq.v1 --property print.headers=true` shows `error-reason`.
8. Exports page: request an XLSX, watch the job go queued -> running -> succeeded, download (15-minute presigned URL).

## Out of scope for v1 (future work)

* Exactly-once processing across external systems
* Machine learning forecasting
* Multi-region replication
* Custom report builder
* Mobile app

## Secrets

`.env` is git-ignored and holds locally generated random values. `.env.example` documents variable names only.
Plaintext producer API keys/passwords exist only in the git-ignored `seed-output/` file the seed script writes.
