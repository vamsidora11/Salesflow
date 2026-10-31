# Real-Time Sales Analytics Dashboard: Implementation Plan

A system that ingests sales events from many stores or point-of-sale sources, streams them through Kafka, stores them in TimescaleDB, aggregates them continuously, and pushes live updates to a React dashboard over WebSockets. Scheduled jobs produce reports, and users can export data to CSV or Excel.

---

## 1. Goals and Scope

### Goals
- Demonstrate event-driven architecture: producers, a durable log, consumers, and idempotent processing.
- Show time-series data engineering: hypertables, continuous aggregates, retention and compression.
- Deliver a live, responsive dashboard that updates within seconds of an event.
- Show operational maturity: retries, dead-letter handling, consumer lag monitoring, and scheduled jobs.

### In Scope (MVP)
- Ingestion API that accepts single and batched sales events from authenticated sources
- Kafka as the event backbone, with a dead-letter topic
- A consumer service that validates and persists events to TimescaleDB
- Continuous aggregates for hourly and daily sales by store, product, and region
- Analytics query API: summary KPIs, time series, top products, sales by region
- WebSocket channel that pushes aggregate updates to the dashboard in near real time
- Scheduled jobs (Celery Beat): nightly reconciliation, report generation, export file cleanup
- Exports to CSV (streamed) and Excel (async for large datasets)
- React dashboard with live charts, filters, connection status, and export buttons
- Event simulator for demos and load testing

### Out of Scope (v1, list as future work in README)
- Exactly-once processing across external systems
- Machine learning forecasting
- Multi-region replication
- Custom report builder
- Mobile app

---

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI | Async, WebSocket support, automatic OpenAPI docs |
| Event streaming | Apache Kafka 3.x in KRaft mode (no ZooKeeper) | Durable, replayable log; standard in data-heavy companies |
| Kafka client | confluent-kafka (Python) | Production-grade librdkafka wrapper, high throughput |
| Database | PostgreSQL 16 + TimescaleDB extension | Time-series hypertables, continuous aggregates, compression, SQL |
| Cache and pub/sub | Redis 7 | Fan-out of live updates to WebSocket servers, rate limits, job state |
| Background jobs | Celery 5 + Celery Beat (Redis broker) | Scheduled and long-running export jobs |
| Object storage | MinIO (S3-compatible) locally, AWS S3 in production | Stores generated export files, served by presigned URLs |
| Excel generation | XlsxWriter | Fast, low-memory writing for large files |
| Frontend | React 18 + Vite + TypeScript | Same toolchain as other projects |
| Charts | Recharts | Declarative React charts, good for live updates |
| Server state | TanStack Query | Caching and refetch on reconnect |
| Monitoring | Prometheus metrics, Grafana (optional), structured logs | Consumer lag and throughput visibility |
| Testing | pytest, testcontainers (Kafka, Timescale, Redis), Vitest | Real infrastructure in integration tests |
| Containers | Docker, Docker Compose | Runs the full pipeline locally with one command |

Note on Kafka vs RabbitMQ: Kafka is the default because replay and consumer groups demonstrate more streaming concepts. RabbitMQ is an acceptable substitute if Kafka is too heavy for your machine; section 17 explains the changes.

---

## 3. Architecture

```
  Sales sources (POS, simulator, CSV upload)
            |  HTTPS POST /api/v1/ingest/events  (API key)
            v
+-----------------------------+
|  Ingestion API (FastAPI)    |  validate -> produce to Kafka -> 202 Accepted
+-------------+---------------+
              |
              v
+-------------------------------------------+
|  Kafka                                    |
|   topic: sales.events.v1   (key=store_id) |
|   topic: sales.events.dlq.v1              |
+------+------------------------------------+
       |  consumer group: sales-writer
       v
+-----------------------------+        +----------------------+
|  Stream consumer worker     |------->|  Redis pub/sub       |
|  validate, dedupe, persist, |        |  channel per tenant  |
|  publish live update        |        +----------+-----------+
+-------------+---------------+                   |
              |                                   v
              v                       +-----------------------+
+-----------------------------+       | WebSocket endpoint    |
| TimescaleDB                 |<------| (FastAPI, same app)   |
|  sales_events (hypertable)  |       +-----------+-----------+
|  sales_hourly  (cont. agg.) |                   |
|  sales_daily   (cont. agg.) |                   v
+-------------+---------------+       +-----------------------+
              ^                       | React dashboard       |
              |                       +-----------+-----------+
+-------------+---------------+                   |
| Celery worker + Celery Beat |                   |  REST (queries, exports)
|  scheduled reports, exports |<------------------+
|  reconciliation, cleanup    |
+-------------+---------------+
              |
              v
        MinIO / S3 (export files)
```

### Layering rules (same discipline as the TaskFlow project)
- Routers handle HTTP and WebSocket framing only.
- Services hold business logic (validation rules, aggregation queries, export preparation).
- Repositories are the only code that runs SQL.
- Kafka producers and consumers are infrastructure adapters, called from services, never from routers directly.
- Pydantic schemas are separate from ORM models.

### Process model
Run three separate processes from the same codebase, so each can be scaled independently:
1. `api`: FastAPI app serving REST and WebSockets
2. `consumer`: Kafka consumer loop (`python -m app.workers.consumer`)
3. `worker` and `beat`: Celery worker and Celery Beat scheduler

---

## 4. Data Model

### Relational reference tables (regular PostgreSQL tables)

```
tenants
  id (uuid), name, created_at

stores
  id (uuid), tenant_id, name, region, timezone, api_key_hash (unique), is_active, created_at

products
  id (uuid), tenant_id, sku, name, category, list_price NUMERIC(12,2), created_at
  UNIQUE (tenant_id, sku)

users
  id (uuid), tenant_id, email (unique), password_hash, role (viewer|analyst|admin), created_at

export_jobs
  id (uuid), tenant_id, requested_by, format (csv|xlsx), filters JSONB,
  status (queued|running|succeeded|failed), file_key, error, row_count,
  created_at, started_at, completed_at, expires_at
```

### Time-series fact table (TimescaleDB hypertable)

```
sales_events
  event_id     uuid            -- from the producer, used for deduplication
  event_time   timestamptz     -- when the sale happened, in UTC
  received_at  timestamptz     -- when the platform accepted it
  tenant_id    uuid
  store_id     uuid
  product_id   uuid
  quantity     integer CHECK (quantity > 0)
  unit_price   NUMERIC(12,2)
  amount       NUMERIC(12,2)   -- quantity * unit_price, computed by the producer and verified by the consumer
  currency     char(3)
  channel      text            -- pos | online | mobile
  PRIMARY KEY (event_time, event_id, product_id)   -- one row per line item; partitioning column must be in the PK

-- Convert to hypertable, chunked by day
SELECT create_hypertable('sales_events', 'event_time', chunk_time_interval => INTERVAL '1 day');
```

Why the primary key includes `event_time`: TimescaleDB requires the partitioning column in every unique constraint. Retries of the same event carry the same `event_time`, so `ON CONFLICT (event_time, event_id) DO NOTHING` still deduplicates.

### Continuous aggregates

```sql
CREATE MATERIALIZED VIEW sales_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', event_time) AS bucket,
       tenant_id, store_id, product_id,
       sum(amount)   AS revenue,
       sum(quantity) AS units,
       count(*)      AS transactions
FROM sales_events
GROUP BY bucket, tenant_id, store_id, product_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('sales_hourly',
  start_offset => INTERVAL '3 days',
  end_offset   => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute');
```

Create a similar `sales_daily` aggregate on `time_bucket('1 day', ...)`, with `sales_hourly` as the base for rollups where possible.

### Retention and compression

| Data | Compression | Retention |
|---|---|---|
| `sales_events` raw | Compress chunks older than 7 days | Drop chunks older than 90 days (configurable) |
| `sales_hourly` | Compress after 30 days | Keep 2 years |
| `sales_daily` | None needed | Keep indefinitely |

Retention and compression are set with `add_retention_policy` and `add_compression_policy`. Document the reasoning in the README: raw events are expensive to keep, aggregates are cheap and answer most questions.

### Indexes
- Hypertable indexes on `(tenant_id, store_id, event_time DESC)` and `(tenant_id, product_id, event_time DESC)`.
- Continuous aggregates inherit tenant-scoped queries; check with `EXPLAIN ANALYZE` that chunk exclusion works for time-range filters.

---

## 5. Event Contract

Events are the most important interface in this system. Define them once and version them.

### Event schema (version 1)

```json
{
  "event_id": "7b1e4c2a-9d3f-4e8a-b6c1-2f5d7a9e0c11",
  "event_type": "sale.completed",
  "event_version": 1,
  "event_time": "2026-10-30T14:22:05Z",
  "store_id": "3f2a9c1e-6b7d-4a8e-9f0c-1d2e3f4a5b6c",
  "channel": "pos",
  "currency": "USD",
  "items": [
    { "product_id": "a1b2c3d4-...", "quantity": 2, "unit_price": "19.99" },
    { "product_id": "e5f6a7b8-...", "quantity": 1, "unit_price": "5.00" }
  ]
}
```

Design decisions to explain in interviews:
- **One event per transaction, with line items**, but the stored fact table is one row per item (normalized for aggregation). The consumer explodes items into rows sharing the same `event_id`, so the composite key is `(event_time, event_id, product_id)` as defined in section 4.
- **Money as strings in JSON, Decimal in Python, NUMERIC in PostgreSQL.** JSON numbers are floats in many languages, so amounts are sent as strings.
- **`event_id` is generated by the producer**, not the server, so retries from the producer are deduplicated.
- **`event_version`** allows the schema to evolve. The consumer handles version 1 now and rejects unknown versions to the DLQ.

### Kafka topics

| Topic | Partitions | Key | Retention | Purpose |
|---|---|---|---|---|
| `sales.events.v1` | 6 | `store_id` | 7 days | Main event stream. Keying by store keeps each store's events ordered. |
| `sales.events.dlq.v1` | 3 | `event_id` | 30 days | Events that failed validation or repeatedly failed processing |

### Message headers
- `tenant-id`, `schema-version`, `produced-at`, `request-id` (for tracing from ingestion to database)

---

## 6. Ingestion API

### Authentication for producers
Each store (or POS integration) has an API key. The key is sent in the `X-API-Key` header. The database stores only a SHA-256 hash. Keys can be rotated and revoked. This is separate from dashboard user authentication (JWT).

### Behaviour
1. Authenticate the API key and resolve `tenant_id` and `store_id`.
2. Validate the batch: maximum 500 events per request, maximum 256 KB body size.
3. Validate each event against the schema. Invalid events are returned in the response, not silently dropped.
4. Check that the event's `store_id` matches the API key's store.
5. Produce valid events to Kafka with the store as the key. Wait for broker acknowledgement (`acks=all`).
6. Return `202 Accepted` with counts and the list of rejected events.

Returning 202 means "accepted into the durable log", not "saved to the database". The dashboard therefore updates a few hundred milliseconds to seconds later. Document this difference clearly.

### Rate limiting
Per API key, using the Redis limiter pattern from the TaskFlow project (for example 50 requests per second with a burst allowance).

### Backpressure
If Kafka is unavailable, the endpoint returns `503` with `Retry-After`. The producer must not buffer unbounded data in memory.

---

## 7. Stream Processing (Consumer)

### Processing rules
1. Poll a batch of messages (for example up to 500 or 1 second).
2. Parse and validate each message.
3. Invalid messages go to `sales.events.dlq.v1` with the error reason in headers. The main offset still advances.
4. Explode line items into rows.
5. Insert in one database transaction with `ON CONFLICT DO NOTHING`.
6. Only after the database commit succeeds, commit the Kafka offsets. This gives at-least-once delivery, and the idempotent insert makes it effectively once.
7. After commit, publish a small update message per affected `(tenant, store, hour bucket)` to Redis for live dashboards.

### Failure handling
- Transient database errors: retry with exponential backoff (for example 0.5s, 1s, 2s, 4s, then pause the partition).
- Repeated failures for the same message: send to DLQ after N attempts so one bad message cannot block a partition forever.
- Consumer crash: on restart, Kafka redelivers from the last committed offset. Step 5 makes this safe.
- Redis publish failure: log and continue. Live updates are best-effort; the database is the source of truth, and clients recover through backfill (section 9).

### Consumer observability
Expose Prometheus metrics:
- `sales_events_consumed_total{result="ok|invalid|duplicate|error"}`
- `sales_consumer_lag` (messages behind the head, per partition)
- `sales_batch_duration_seconds` histogram
- `sales_dlq_total`

### Scaling
Run multiple consumer instances in the same consumer group. Kafka assigns partitions across them. The maximum useful parallelism equals the number of partitions (6 in this plan).

---

## 8. Aggregation and Scheduled Jobs

### Continuous aggregates (real-time layer)
TimescaleDB refreshes `sales_hourly` and `sales_daily` on a schedule (every minute by default). Queries for the last few days read these tables instead of scanning raw events. This keeps dashboard queries fast regardless of raw data volume.

### Celery Beat schedule (batch layer)

| Job | Schedule | Purpose |
|---|---|---|
| `reconcile_daily_totals` | Daily at 01:00 | Compare aggregate totals with raw event totals per store. Log and alert on any difference. |
| `generate_daily_report` | Daily at 06:00 per tenant | Build a summary (JSON and PDF or XLSX) and store it in object storage. |
| `cleanup_expired_exports` | Hourly | Delete export files whose `expires_at` has passed and mark the jobs expired. |
| `refresh_dead_letter_summary` | Every 15 minutes | Count DLQ entries by error reason and record them as metrics. |

### Airflow option
Airflow is an alternative for the batch layer, with DAG visualisation and retry UI. Celery Beat is the default because it is lighter to run locally. If you want Airflow for a portfolio signal, replace the Beat schedules with one DAG that has the same four tasks, and keep the job functions unchanged so the business logic is reusable.

---

## 9. Real-Time Updates (WebSockets)

### Flow
1. The consumer publishes a message to Redis channel `analytics:{tenant_id}` after each committed batch. The message contains only the changed buckets and new totals, not raw events.
2. Every API instance subscribes to Redis for the tenants that have active WebSocket connections.
3. The WebSocket handler forwards updates to the matching connected dashboards.

### WebSocket endpoint: `WS /api/v1/ws/dashboard`

Authentication: the client connects, then sends the first message `{"type": "auth", "token": "<jwt>"}` within 5 seconds. Otherwise the server closes the connection. Do not put the token in the URL, because URLs end up in logs and browser history.

Message types from server:
- `auth.ok` / `auth.error`
- `update`: `{ "type": "update", "seq": 1042, "bucket": "2026-10-30T14:00Z", "store_id": "...", "revenue": "1523.40", "units": 88 }`
- `heartbeat` every 25 seconds
- `resync_required`: sent if the client is too far behind (see below)

Message types from client:
- `auth`
- `subscribe` / `unsubscribe` with a filter (store_id or region)
- `ping`

### Sequence numbers and backfill
- Each update has a monotonically increasing `seq` per tenant, stored in Redis with `INCR`.
- The client tracks the last `seq` it applied.
- After reconnect, the client calls `GET /api/v1/analytics/timeseries?since_seq=...` or simply refetches the current window. If the gap is too large, the server sends `resync_required` and the client reloads.
- This is a good talking point: real-time systems must handle disconnects, and the database remains the source of truth.

### Backpressure for slow clients
If a client's send buffer grows beyond a limit, the server drops intermediate updates for that client and sends `resync_required`. Updates for the same bucket are coalesced, so only the latest value is sent.

---

## 10. Analytics Query API

All endpoints are tenant-scoped via the JWT and require the `analyst` or `admin` role unless noted. Viewers can read summary data only.

| Endpoint | Purpose | Source table |
|---|---|---|
| `GET /api/v1/analytics/summary?from=&to=&store_id=` | KPIs: revenue, units, transactions, average order value | `sales_daily` |
| `GET /api/v1/analytics/timeseries?granularity=hour\|day&metric=revenue\|units&from=&to=` | Chart data | `sales_hourly` or `sales_daily` |
| `GET /api/v1/analytics/top-products?limit=10&from=&to=` | Ranking by revenue | `sales_daily` |
| `GET /api/v1/analytics/by-region?from=&to=` | Revenue by region | `sales_daily` joined to `stores` |
| `GET /api/v1/analytics/by-channel?from=&to=` | Revenue by channel | `sales_hourly` |
| `GET /api/v1/stores` / `GET /api/v1/products` | Filter lookups | Relational tables |

### Query rules
- Time ranges are required and capped (for example maximum 366 days for hourly data) to protect the database.
- Pagination on list endpoints using cursor pagination (same convention as TaskFlow).
- Responses include `generated_at` and `data_freshness_seconds` so the UI can show how fresh the numbers are.
- Currency: all revenue values include the currency code. Mixed currencies are converted at the store's configured rate, or grouped separately. Pick one approach and document it.

### Swagger
Apply the same OpenAPI conventions as the TaskFlow project: summaries, tags, stable operation IDs, documented error responses, examples, and a committed `docs/openapi.json` with a CI drift check. The WebSocket endpoint is not documented by OpenAPI, so describe its messages in `docs/websocket.md`.

---

## 11. Data Export

### Synchronous export: CSV (streamed)
- `GET /api/v1/exports/csv?from=&to=&granularity=day&store_id=`
- Streams rows with `StreamingResponse` so memory stays flat even for large ranges.
- Uses a server-side database cursor (`yield_per`) instead of loading all rows.
- Limit: up to 1 million rows synchronously. Above that, use an async export.

### Asynchronous export: Excel and large CSV
1. `POST /api/v1/exports` with `{ "format": "xlsx", "filters": {...} }` returns `202` with a job ID.
2. A Celery task runs the query in chunks, writes the file with XlsxWriter to a temporary location, and uploads it to object storage.
3. The job status changes to `succeeded` with a `file_key`.
4. `GET /api/v1/exports/{id}` returns status and progress.
5. `GET /api/v1/exports/{id}/download` returns a presigned URL valid for 15 minutes.
6. Files expire after 7 days, and `cleanup_expired_exports` removes them.

### Excel rules
- Sheet 1: Summary (KPIs and filter values used)
- Sheet 2: Data (one row per day or hour, store, and product)
- Freeze the header row, set column types (dates as dates, money as numbers with two decimals), and include autofilter.
- Limit to Excel's maximum of 1,048,576 rows per sheet. Split into more sheets if needed, and document it.

### Export security
- Exports are scoped to the requester's tenant and filters are validated before the query runs.
- Presigned URLs expire quickly and are not logged.
- Each export records who requested it in `export_jobs` for audit.

---

## 12. Frontend Dashboard

### Pages and components
- **Login** (JWT, same pattern as the TaskFlow project)
- **Dashboard**: KPI cards (revenue, units, transactions, average order value), a live revenue line chart, a units bar chart, a top-products table, a region breakdown pie or bar chart, and a channel split chart.
- **Filters bar**: date range, store, region, and channel. Changing filters refetches data.
- **Live indicator**: connected, reconnecting, or offline, plus "last updated N seconds ago".
- **Exports page**: request an export, see job status, and download the file.
- **Stores and products** (admin): list and create.

### Live update behaviour
- A `useLiveDashboard` hook manages the WebSocket: authenticates, subscribes, applies updates to the TanStack Query cache, and reconnects with exponential backoff.
- Updates are applied to the cache with `queryClient.setQueryData`, so charts re-render without a full refetch.
- Batch UI updates (for example every 500 ms) so a burst of messages does not cause dozens of re-renders.
- On `resync_required`, invalidate the affected queries and refetch.

### Recharts usage
- Use `ResponsiveContainer` so charts resize.
- Animate new points but disable animation for high-frequency updates to keep the UI smooth.
- Keep chart data arrays bounded (for example the last 24 hours of hourly buckets).

### Accessibility and UX
- Every chart has a text summary or accessible table alternative.
- Colour choices are checked for colour-blindness.
- Empty and loading states are shown on every widget.

---

## 13. Repository Structure

```
salesflow/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app factory, routers, OpenAPI config
│   │   ├── core/
│   │   │   ├── config.py              # pydantic-settings
│   │   │   ├── security.py            # JWT, API key hashing
│   │   │   ├── logging.py             # structured JSON logging
│   │   │   └── metrics.py             # Prometheus counters and histograms
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   └── models/                # relational models and hypertable definitions
│   │   ├── schemas/                   # Pydantic: events, analytics, exports, auth
│   │   ├── repositories/              # SQL for events, aggregates, exports
│   │   ├── services/                  # ingestion, analytics, export, notifications
│   │   ├── streaming/
│   │   │   ├── producer.py            # confluent-kafka producer wrapper
│   │   │   ├── consumer.py            # consumer loop, batching, offsets
│   │   │   ├── dlq.py                 # dead-letter handling
│   │   │   └── event_parser.py        # schema validation and line-item explosion
│   │   ├── realtime/
│   │   │   ├── pubsub.py              # Redis publish and subscribe
│   │   │   ├── websocket_manager.py   # connections, filters, coalescing
│   │   │   └── sequence.py            # per-tenant sequence numbers
│   │   ├── tasks/                     # Celery app, beat schedule, export and report tasks
│   │   ├── storage/                   # S3/MinIO client and presigned URLs
│   │   └── api/
│   │       ├── deps.py
│   │       └── v1/                    # ingest, analytics, exports, ws, stores, products, health
│   ├── migrations/                    # Alembic, including Timescale setup
│   ├── scripts/
│   │   ├── export_openapi.py
│   │   └── seed_demo_data.py
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── streaming/                 # consumer, DLQ, idempotency tests
│   │   ├── realtime/                  # WebSocket auth and resync tests
│   │   └── load/                      # load test scripts
│   ├── pyproject.toml
│   └── Dockerfile
├── simulator/
│   ├── simulate_sales.py              # generates realistic events at a configurable rate
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/                       # typed client, generated from openapi.json
│   │   ├── realtime/                  # useLiveDashboard hook
│   │   ├── features/                  # analytics, exports, stores
│   │   ├── components/                # charts, KPI cards, filters, live indicator
│   │   └── pages/
│   ├── package.json
│   └── Dockerfile
├── infra/
│   ├── nginx/nginx.conf               # proxies /api and /ws, serves frontend
│   ├── kafka/create-topics.sh
│   ├── timescale/init.sql             # extensions and roles
│   └── prometheus/prometheus.yml
├── docs/
│   ├── openapi.json
│   ├── websocket.md
│   └── architecture.md
├── docker-compose.yml
├── .github/workflows/ci.yml
├── .env.example
├── CLAUDE.md
└── README.md
```

---

## 14. Docker Compose Services

| Service | Image | Purpose |
|---|---|---|
| `timescaledb` | `timescale/timescaledb:latest-pg16` | Primary database |
| `kafka` | `apache/kafka:3.7.0` (KRaft single node locally) | Event log |
| `kafka-init` | Same as kafka | Creates topics from `infra/kafka/create-topics.sh` then exits |
| `redis` | `redis:7` | Pub/sub, rate limits, Celery broker |
| `minio` | `minio/minio` | Export file storage |
| `api` | Built from backend | REST and WebSocket server |
| `consumer` | Built from backend | Kafka consumer process |
| `worker` | Built from backend | Celery worker |
| `beat` | Built from backend | Celery Beat scheduler |
| `simulator` | Built from simulator | Sends sample events (behind a profile, so it runs only on demand) |
| `frontend` | Built from frontend | Dashboard build |
| `nginx` | `nginx:1.27` | Reverse proxy for `/api`, `/ws`, and the frontend |
| `prometheus` | `prom/prometheus` | Scrapes `/metrics` (optional profile) |

Use Compose profiles: `core` for the required services, `demo` for the simulator, `monitoring` for Prometheus and Grafana.

---

## 15. Testing Strategy

| Level | Tool | What it covers |
|---|---|---|
| Unit | pytest | Event validation, line-item explosion, amount checks, sequence logic, coalescing |
| Integration | pytest + testcontainers (Timescale, Kafka, Redis, MinIO) | Repositories, aggregates, migrations, Kafka round-trip |
| Streaming | pytest + Kafka container | Consumer writes once when a message is delivered twice; invalid messages reach the DLQ; offsets commit only after DB commit; restart resumes correctly |
| Realtime | pytest + WebSocket client | Auth timeout, invalid token rejected, subscribed filters respected, cross-tenant updates never delivered, resync sent for a slow client |
| API | pytest + httpx | Each endpoint: success, validation error, auth error, time-range limit |
| Export | pytest | CSV streams correctly, Excel has expected sheets and types, presigned URLs expire |
| Security | pytest, dedicated suite | Tenant isolation in queries, API key scope, WebSocket tenant separation, no secrets in logs |
| Load | k6 or Locust (scripts in `tests/load/`) | Ingestion throughput and dashboard update latency |
| Frontend | Vitest + React Testing Library | Hook reconnect logic, cache updates, empty and error states |

### Load and performance targets (local laptop baseline)
- Ingestion: sustain 2,000 events per second for 5 minutes with no HTTP 5xx responses.
- End-to-end latency: 95% of events visible on the dashboard within 3 seconds of ingestion.
- Consumer lag: returns to zero within 30 seconds after the load test stops.
- Dashboard summary query: under 200 ms with 10 million raw events, served from aggregates.

Record the results in `docs/performance.md` with the hardware specs. Reviewers value measured numbers.

---

## 16. Phased Implementation Plan

Each phase ends with a working, tested, committed state. Do not start a phase until the previous one passes its acceptance criteria.

### Phase 0: Setup and Infrastructure (2 to 3 days)
**Tasks**
- Monorepo structure from section 13, Python 3.12 backend with `pyproject.toml`, Vite React frontend.
- Docker Compose with `timescaledb`, `kafka` (KRaft), `kafka-init`, `redis`, `minio`, `api`, `frontend`, `nginx`.
- `kafka-init` creates `sales.events.v1` and `sales.events.dlq.v1` with the partitions from section 5.
- FastAPI app with health endpoints (`/health`, `/health/ready` checking Postgres, Redis, and Kafka) and Swagger configuration.
- `.env.example`, Ruff, mypy, pytest, GitHub Actions CI.

**Acceptance criteria**
- `docker compose up` starts all infrastructure services healthy.
- Topics exist with correct partition counts (verify with `kafka-topics --describe`).
- `/health/ready` returns 200 only when all dependencies are reachable.
- CI is green.

### Phase 1: Data Model and TimescaleDB (3 to 4 days)
**Tasks**
- Relational models and Alembic migrations for tenants, stores, products, users, export_jobs.
- Create `sales_events` as a hypertable with the composite primary key from section 4.
- Create `sales_hourly` and `sales_daily` continuous aggregates and their policies.
- Compression and retention policies.
- Seed script that creates two tenants, stores in different regions, and 50 products.

**Acceptance criteria**
- Migrations run up and down cleanly.
- Inserting a row shows up in `sales_hourly` after the refresh window (test with a manual `refresh_continuous_aggregate` call).
- `EXPLAIN ANALYZE` on a one-week time-range query shows chunk exclusion.
- Retention policy job is registered (verify with `timescaledb_information.jobs`).

### Phase 2: Event Contract and Ingestion API (3 to 4 days)
**Tasks**
- Pydantic event schema (version 1) and validation rules.
- API key model, generation, hashing, and authentication dependency.
- Kafka producer wrapper with `acks=all`, idempotent producer enabled, and delivery callbacks.
- `POST /api/v1/ingest/events` with batch handling, per-event results, and 202 response.
- Rate limiting per API key.
- Producer returns 503 when Kafka is unavailable.

**Acceptance criteria**
- A batch of 500 events is produced to Kafka and visible with a console consumer.
- Events for another store's ID are rejected with a clear per-event error.
- Invalid events are reported in the response and not produced.
- Test with Kafka stopped: the API returns 503 and does not crash.

### Phase 3: Stream Processing Consumer (4 to 5 days)
**Tasks**
- Consumer loop with batching, manual offset commit, and graceful shutdown on SIGTERM.
- Validation, line-item explosion, and `ON CONFLICT DO NOTHING` insert.
- Retry with exponential backoff and DLQ routing after N attempts.
- Prometheus metrics for consumed, invalid, duplicate, and error counts, plus lag.
- Post-commit publish to Redis (placeholder subscriber until Phase 5).

**Acceptance criteria**
- Sending the same event twice results in one row in `sales_events`.
- Killing the consumer mid-batch and restarting produces no duplicates and no losses (test this explicitly).
- A malformed message lands in `sales.events.dlq.v1` and the partition keeps moving.
- Consumer lag metric is exposed and is near zero under light load.

### Phase 4: Analytics Queries and Scheduled Jobs (4 to 5 days)
**Tasks**
- Analytics repositories querying continuous aggregates.
- Endpoints: summary, timeseries, top-products, by-region, by-channel.
- Time-range validation and caps.
- Celery app with Redis broker, Celery Beat schedule from section 8.
- Implement `reconcile_daily_totals` and `cleanup_expired_exports` first, then the others.

**Acceptance criteria**
- Summary and timeseries values match a direct SQL aggregation over raw events (test with seeded data).
- Celery Beat triggers jobs on schedule (verify with a short test interval).
- Reconciliation job logs a mismatch when a deliberate discrepancy is inserted.

### Phase 5: Real-Time Push (4 to 5 days)
**Tasks**
- Redis pub/sub publishing from the consumer and subscribing from the API.
- WebSocket endpoint with first-message authentication and a 5-second timeout.
- Per-tenant isolation in the connection manager.
- Subscription filters (store, region), heartbeats, and ping handling.
- Per-tenant sequence numbers and `resync_required` logic.
- Write `docs/websocket.md`.

**Acceptance criteria**
- A test client receives an update within 3 seconds of a sale being ingested.
- A connection without valid auth is closed after 5 seconds.
- A user in tenant A never receives tenant B updates (test this).
- A slow client receives `resync_required` instead of an unbounded backlog.

### Phase 6: Exports (3 to 4 days)
**Tasks**
- Streaming CSV endpoint with server-side cursor.
- Export job model, `POST /exports`, status and download endpoints.
- Celery task that writes XLSX with XlsxWriter in chunks, uploads to MinIO, and updates job status.
- Presigned URL generation with a 15-minute expiry.
- Export size limits and validation.

**Acceptance criteria**
- A CSV export of 1 million rows streams without the API process exceeding a fixed memory budget (measure it).
- An XLSX export opens in Excel or LibreOffice with the expected sheets, types, and frozen headers.
- The download URL stops working after 15 minutes.
- Another tenant cannot access a job ID that is not theirs (test this).

### Phase 7: Frontend Dashboard (6 to 8 days)
**Tasks**
- Typed API client generated from `docs/openapi.json`.
- Login and protected routing.
- Dashboard with KPI cards, charts, top-products table, and filters.
- `useLiveDashboard` hook with reconnect, backoff, cache updates, and batched rendering.
- Live status indicator and "last updated" display.
- Exports page with job polling and download.

**Acceptance criteria**
- With the simulator running, charts update without page refresh.
- Killing the API process shows "reconnecting", and the dashboard recovers and resyncs when it returns.
- Changing filters refetches correct data.
- Export of an XLSX file works end to end from the UI.

### Phase 8: Simulator, Load Testing, Observability, and Deployment (5 to 6 days)
**Tasks**
- Simulator with configurable rate, store count, and realistic product mix and daily patterns.
- Load test scripts and recorded results in `docs/performance.md`.
- Prometheus scrape config and optional Grafana dashboard for ingest rate, consumer lag, and WebSocket connections.
- Structured logs with `request_id` and `tenant_id` flowing from ingestion through to consumer logs.
- Production compose file, secrets handling, deployment notes.
- README with architecture diagram, data flow explanation, and design decisions.

**Acceptance criteria**
- Load test meets the targets in section 15.
- A single request ID can be traced from the ingest log line to the consumer log line to the database row.
- README includes a 2-minute demo script.
- Deployed or runnable with one command for the reviewer.

**Total estimate:** about 8 to 10 weeks part-time, or 4 to 5 weeks full-time.

---

## 17. Open Decisions (defaults used unless changed)

| Decision | Default | Alternative |
|---|---|---|
| Event broker | Kafka (KRaft) | RabbitMQ with quorum queues (replace the streaming module, keep the rest) |
| Kafka client | confluent-kafka | aiokafka (fully async, slightly less throughput) |
| Batch orchestration | Celery Beat | Airflow (DAG per job; reuse the same task functions) |
| Charts | Recharts | Chart.js with react-chartjs-2 (better for very large datasets) |
| Excel library | XlsxWriter | openpyxl (can also read files, slower for large writes) |
| Object storage | MinIO locally, S3 in production | Local volume (simplest, not realistic for a portfolio) |
| WebSocket auth | First-message JWT | Query-string token (simpler, but tokens leak into logs) |
| Currency handling | Single currency per tenant | Multi-currency with daily exchange rates table |
| Deployment | Single VM with Compose, or Render with managed Kafka alternative | AWS MSK, Confluent Cloud, or Redpanda Cloud |

---

## 18. Working With Claude Code

### CLAUDE.md template

```markdown
# CLAUDE.md

## Project
Real-time sales analytics platform. Event-driven: API -> Kafka -> consumer -> TimescaleDB -> Redis pub/sub -> WebSocket -> React.
Read SALES_ANALYTICS_PLAN.md before starting any task.

## Stack
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, confluent-kafka, Celery, Redis, TimescaleDB (PostgreSQL 16)
- Frontend: React 18, Vite, TypeScript, TanStack Query, Recharts

## Rules
- Routers never call Kafka, Redis, or SQL directly. Go through services and adapters (section 3).
- Commit Kafka offsets only after the database transaction commits.
- All database inserts into sales_events use ON CONFLICT (event_time, event_id, product_id) DO NOTHING.
- Money: Decimal in Python, NUMERIC in SQL, strings in JSON. Never float.
- Timestamps: timezone-aware UTC everywhere.
- Every endpoint is tenant-scoped. Return 404 for another tenant's resource.
- Never put tokens in URLs or logs.
- Live updates are best-effort; the database is the source of truth.
- Every new event field or topic change requires a schema version bump and an update to section 5.

## Commands
- Start infrastructure: `docker compose up -d timescaledb kafka redis minio`
- Backend tests: `cd backend && pytest`
- Lint and types: `ruff check . && mypy app`
- Migrations: `alembic upgrade head`
- Run consumer: `python -m app.streaming.consumer`
- Simulate traffic: `docker compose --profile demo up simulator`

## Workflow
- Work one phase at a time from section 16. Stop at the end of each phase and summarize.
- Write tests in the same change as the code.
- Update docs/openapi.json when API changes.
- Update PROGRESS.md at the end of each session.
```

### Phase prompt (Phase 0)

```
Read SALES_ANALYTICS_PLAN.md and CLAUDE.md completely before doing anything.

Implement Phase 0 only (section 16). Do not start later phases.

Before writing code:
1. Summarize the architecture in 5 to 8 sentences.
2. List the files and folders you will create, following section 13.
3. List your assumptions. Use the defaults in section 17 where the plan is ambiguous, and state them.
4. Wait for my approval.

After approval, implement Phase 0 with the acceptance criteria in section 16. Add tests for the health endpoints. Run lint, type checks, and tests before reporting.

When finished, stop and give me the exact commands to verify the infrastructure, any deviations from the plan with reasons, and the next steps.
```

### Subsequent phases
Use the same structure as Phase 0, replacing the phase number. Always ask Claude Code to show the Kafka and database evidence for streaming and aggregation phases, not just passing tests. For example: "Show me the topic offsets and the sales_events row count before and after the duplicate test."

---

## 19. Definition of Done

- [ ] Ingestion API accepts batches, authenticates producers, and returns per-event results
- [ ] Events flow through Kafka to TimescaleDB with idempotent writes
- [ ] Invalid events reach the DLQ without blocking the partition
- [ ] Consumer survives restarts with no duplicates and no losses (tested)
- [ ] Continuous aggregates and retention and compression policies are active
- [ ] Analytics endpoints return values that match raw-event SQL (tested)
- [ ] Scheduled Celery Beat jobs run and the reconciliation job detects mismatches
- [ ] WebSocket delivers live updates, enforces tenant isolation, and resyncs slow or reconnecting clients
- [ ] CSV streams and XLSX export work asynchronously with expiring download links
- [ ] Dashboard shows live charts, filters, connection status, and exports
- [ ] Load test results recorded in docs/performance.md against the targets in section 15
- [ ] Swagger documents every REST endpoint; docs/websocket.md documents the socket messages
- [ ] Traceable request IDs across ingestion, consumer, and database logs
- [ ] README explains architecture, delivery guarantees, and design decisions
- [ ] Can explain at-least-once delivery, idempotency, and why aggregates are used for queries
