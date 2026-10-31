# PROGRESS

## Status
All phases (0-8) have code, unit tests and docs written. **Nothing that needs Docker has been run**: the
machine had no Docker. Treat every container-dependent item below as unverified until its command has been run.

## Phase checklist
- [x] Phase 0: monorepo, health endpoints, Compose, topic script, CI (container acceptance criteria unverified)
- [x] Phase 1: models, Alembic migrations 0001-0003 (hypertable, aggregates, policies), seed script (migrations unverified on a real DB)
- [x] Phase 2: event contract, API keys, idempotent producer, `POST /ingest/events`, rate limit, 503 on Kafka outage
- [x] Phase 3: consumer (batching, manual commit after DB commit, retry/backoff, DLQ, metrics, SIGTERM)
- [x] Phase 4: analytics repos/endpoints, Celery app + Beat schedule, reconcile / cleanup / report / DLQ-summary jobs
- [x] Phase 5: Redis pub/sub, WebSocket (first-message auth, filters, heartbeats, sequence numbers, coalescing, resync), docs/websocket.md
- [x] Phase 6: streamed CSV, async export jobs, XLSX writer, presigned URLs, tenant-scoped job access
- [x] Phase 7: React dashboard (login, live charts, filters, exports, admin), `useLiveDashboard`
- [x] Phase 8: simulator, Locust + latency probe, Prometheus/Grafana, JSON logs with request IDs, prod compose, README, deployment docs

## What was actually run (2026-10-31, Windows, Python 3.13, Node 26)
| Check | Result |
|---|---|
| `ruff check .` / `ruff format --check .` (backend) | pass |
| `mypy app scripts migrations` (strict) | pass |
| `pytest` (backend) | 156 passed, 14 skipped (integration/Kafka tests need Docker) |
| `alembic heads` | single head `0003`, chain 0001 -> 0002 -> 0003 loads |
| `npm test` (frontend, Vitest) | 42 passed |
| `npm run build` (frontend) | pass (chunk-size warning only) |
| `docs/openapi.json` | regenerated; drift test passes |
| `bash -n infra/kafka/create-topics.sh`, compose YAML parse | pass |

## NOT run (needs Docker) - run these first
1. `docker compose up -d --build` then `docker compose ps` (all healthy; `migrate` and `kafka-init` exit 0).
2. `kafka-topics.sh --describe` for both topics (6 and 3 partitions) and `curl -i localhost:8080/health/ready`.
3. `cd backend && pytest` with Docker running: 14 skipped tests cover migrations up/down, hypertable/aggregates/policies,
   chunk exclusion, analytics vs raw SQL, tenant isolation in SQL, reconciliation, export jobs, and the consumer against real
   Kafka (duplicate delivery, DLQ, hard kill + restart).
4. Load tests and `docs/performance.md` (currently "NOT MEASURED").
5. `celery beat` firing on schedule against Redis (only the schedule definition is unit-tested).
6. Frontend in a browser against the live stack (only unit/component tests ran; also the `Dockerfile`/`nginx.conf` builds).

## Known risks to check when first run
- Timescale DDL in migration 0003 (hierarchical continuous aggregate on a compressed hourly aggregate, `materialized_only=false`,
  compression policy on an aggregate) has not been executed; adjust if the installed TimescaleDB version rejects any option.
- MinIO healthcheck (`mc ready local`), curl inside `nginx:1.27`, Kafka KRaft listener settings.
- `docker-compose.prod.yml` uses `!reset`/`!override` (Compose >= 2.24).
- Python 3.12 is the target; local checks ran on 3.13.

## Decisions and deviations
See README "Defaults used" and "Deviations from the plan". Frontend gaps: `by-region` ignores the region filter and
`by-channel` ignores the channel filter (the API does not accept them); viewers do not open the WebSocket; JWT is stored in
sessionStorage (XSS tradeoff noted in code); no token refresh endpoint, so 4401 sends users to login.
