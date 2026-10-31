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
- Start infrastructure only: `docker compose up -d timescaledb kafka kafka-init redis minio`
- Full stack: `docker compose up -d --build`; demo data: `docker compose run --rm seed`
- Backend tests: `cd backend && pytest`
- Lint and types: `ruff check . && ruff format --check . && mypy app scripts migrations`
- Migrations: `alembic upgrade head`
- Run consumer: `python -m app.streaming.consumer`; Celery: `celery -A app.tasks.celery_app:celery_app worker|beat`
- Simulate traffic: `docker compose --profile demo up simulator`; load test: `docs/performance.md`
- Frontend: `cd frontend && npm test && npm run build`; regenerate API types: `npm run gen:api`

## Workflow
- Work one phase at a time from section 16. Stop at the end of each phase and summarize.
- Write tests in the same change as the code.
- Update docs/openapi.json when API changes (`cd backend && python scripts/export_openapi.py`).
- Update PROGRESS.md at the end of each session.
