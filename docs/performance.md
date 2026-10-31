# Performance

**Status: NOT MEASURED.** No load test has been run yet: the machine this code was written on has no
Docker, so the stack could not be started. The harness is ready; fill in the tables below by running it
and replace this notice with the hardware description.

## Targets (plan section 15)

| Metric | Target | Result |
|---|---|---|
| Ingestion throughput | 2,000 events/s sustained for 5 min, no HTTP 5xx | _not measured_ |
| End-to-end latency | 95% of events visible on the dashboard within 3 s of ingestion | _not measured_ |
| Consumer lag after load stops | back to 0 within 30 s | _not measured_ |
| Summary query with 10 M raw events | < 200 ms (served from aggregates) | _not measured_ |

## How to measure

```bash
docker compose up -d --build
docker compose run --rm seed                    # writes seed-output/seed_output.json

# 1. Ingestion throughput (2,000 events/s = 40 users x 4 batches/s x 50 events/batch)
cd backend && locust -f tests/load/locustfile.py --headless -u 40 -r 10 -t 5m --host http://localhost:8080
#    (the default ingest rate limit is 50 req/s per store; with 40 users over 8 stores each store sees
#     ~20 req/s, well under the limit)

# 2. End-to-end latency, run during step 1
pip install websockets
python tests/load/latency_probe.py --samples 100

# 3. Consumer lag: watch sales_consumer_lag (Grafana, `--profile monitoring`, or http://localhost:9100/metrics
#    from inside the consumer container) after stopping locust.

# 4. Query latency with 10 M rows: load events (simulator at high rate, or a COPY), then
docker compose exec timescaledb psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "EXPLAIN (ANALYZE, BUFFERS) SELECT sum(revenue) FROM sales_daily WHERE tenant_id = '<id>' AND bucket >= now() - interval '30 days'"
```

## Hardware / environment

_To fill in: CPU, RAM, disk, OS, Docker resource limits, single-node Kafka._

## Expected bottlenecks (hypotheses to confirm, not measurements)

* The Kafka producer wait for `acks=all` is per request; larger batches amortise it.
* Consumer parallelism is capped by the 6 partitions; add consumers (same group) before tuning SQL.
* `bucket_totals` runs one small raw query per (tenant, store, hour) per batch after commit.
