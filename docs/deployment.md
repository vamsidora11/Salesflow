# Deployment

Target: a single VM running Docker Compose (plan section 17 default). Kafka is single-node, which is fine
for a portfolio deployment; for real durability use a managed Kafka (MSK, Confluent Cloud, Redpanda Cloud)
by pointing `KAFKA_BOOTSTRAP_SERVERS` at it and removing `kafka`/`kafka-init` from the deployment.

## One command

```bash
cp .env.example .env     # replace every CHANGE_ME (python -c "import secrets; print(secrets.token_hex(24))")
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose run --rm seed        # optional demo data (writes seed-output/seed_output.json)
```

`migrate` runs `alembic upgrade head` before the API, consumer, worker and beat start.

## Secrets

* Every secret is an environment variable read from `.env` (git-ignored): `POSTGRES_PASSWORD`,
  `REDIS_PASSWORD`, `MINIO_ROOT_PASSWORD`, `JWT_SECRET`, `GRAFANA_ADMIN_PASSWORD`. Names are documented in
  `.env.example`; real values are never committed.
* With `APP_ENV=production` the API refuses to start if `JWT_SECRET` is the built-in default.
* Producer API keys and user passwords are stored hashed (SHA-256 / scrypt). The seed script prints plaintext
  once into `seed-output/seed_output.json`; delete that file after use.
* On a real host prefer your platform's secret manager (Docker/Compose secrets, SSM, Vault) injecting the same variables.

## Network

* `docker-compose.prod.yml` publishes only nginx (80) and MinIO (9000, for presigned downloads). Put TLS in
  front (Caddy/Traefik/cloud LB) and set `PUBLIC_S3_URL` to the public MinIO/S3 URL so presigned links work from a browser.
* WebSocket traffic uses `/api/v1/ws/dashboard` through the same nginx (`Upgrade` headers are proxied).
* Prometheus scrapes `api:8000/metrics` and `consumer:9100/metrics` on the internal network only; `/metrics`
  is not routed by nginx.

## Operations

| Task | Command |
|---|---|
| Scale consumers (max useful = 6 partitions) | `docker compose up -d --scale consumer=3` |
| Consumer lag | Grafana (`--profile monitoring`) or `sales_consumer_lag` |
| Inspect the DLQ | `docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:9092 --topic sales.events.dlq.v1 --from-beginning --property print.headers=true` |
| Replay events | Reset the group offset: stop consumers, `kafka-consumer-groups.sh --reset-offsets --to-datetime ... --execute --group sales-writer --topic sales.events.v1`, start consumers. Safe: inserts are idempotent. |
| Run migrations | `docker compose run --rm migrate` |
| Test Celery Beat quickly | set `BEAT_TEST_INTERVAL_SECONDS=10` in `.env`, `docker compose up -d beat worker` |

## Backups

TimescaleDB data lives in the `timescale-data` volume: schedule `pg_dump`/`pg_basebackup` (or volume
snapshots). Raw events older than `RAW_RETENTION_DAYS` (default 90) are dropped by policy; hourly aggregates are
kept 2 years and daily aggregates indefinitely, so back up before changing retention.
