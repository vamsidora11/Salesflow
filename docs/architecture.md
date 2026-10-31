# Architecture

The diagram, layering rules and process model are in section 3 of
[SALES_ANALYTICS_PLAN.md](../SALES_ANALYTICS_PLAN.md); the README has the as-built diagram and the list of deviations.

## Module map (backend/app)

| Package | Responsibility |
|---|---|
| `api/` | HTTP and WebSocket framing only: routers (`v1/`), dependencies (`deps.py` wires services, auth, rate limiting), shared response docs |
| `services/` | Business logic: ingestion, analytics, exports, catalog, auth, jobs (batch layer), rate limiting, health |
| `repositories/` | The only code that runs SQL (analytics reads aggregates, `sales_events` writes, export streaming, reconciliation) |
| `streaming/` | Kafka adapters and loops: producer wrapper, consumer (`BatchProcessor`, `handle_batch`), DLQ, event parser |
| `realtime/` | Redis pub/sub, per-tenant sequence numbers, connection manager (filters, coalescing, resync), WebSocket session |
| `tasks/` | Celery app, beat schedule, task wrappers around `services/jobs.py` |
| `storage/` | S3/MinIO client and presigned URLs |
| `db/` | Engine/session helpers and SQLAlchemy models; Alembic migrations live in `backend/migrations` |
| `core/` | Settings, security (JWT, API-key hashing, scrypt), JSON logging with request/tenant context, Prometheus metrics |

## Request / event flow with trace points

1. `POST /api/v1/ingest/events`: middleware assigns `request_id` (echoed as `X-Request-ID`) -> API key resolves tenant + store ->
   rate limit (Redis) -> per-event validation -> Kafka message with headers `tenant-id`, `schema-version`, `produced-at`, `request-id`.
2. Consumer: parse/validate -> DLQ for invalid -> one DB transaction (`ON CONFLICT DO NOTHING`, `request_id` stored on each row)
   -> commit offsets -> for each changed (store, hour) read totals and publish to Redis. `batch_committed` log lists the request IDs in the batch.
3. API fan-out task: Redis `analytics:*` -> `ConnectionManager.broadcast(tenant, msg)` -> filtered, coalesced, per-client writer tasks.
4. Dashboard: REST for history (aggregates), WebSocket for deltas, reconciling refetch every 60 s.

Trace one request: grep the ingest `request` log line for its `request_id`, then the consumer `batch_committed` line's
`request_ids`, then `SELECT * FROM sales_events WHERE request_id = '...'`.

## Data model

See migrations `0001`-`0003` (reference tables; `sales_events` hypertable with compression/retention; hourly + hierarchical daily
continuous aggregates with refresh, compression and retention policies).
