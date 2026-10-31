from prometheus_client import Counter, Gauge, Histogram

# API
INGEST_EVENTS = Counter("sales_ingest_events_total", "Ingested events by outcome", ["result"])
WS_CONNECTIONS = Gauge("sales_ws_connections", "Open dashboard WebSocket connections")

# Consumer (plan section 7)
EVENTS_CONSUMED = Counter(
    "sales_events_consumed_total", "Consumed messages by result", ["result"]
)  # result: ok | invalid | duplicate | error
CONSUMER_LAG = Gauge("sales_consumer_lag", "Messages behind the log head", ["partition"])
BATCH_DURATION = Histogram("sales_batch_duration_seconds", "Consumer batch processing time")
DLQ_TOTAL = Counter("sales_dlq_total", "Messages sent to the dead-letter topic")
DLQ_BY_REASON = Gauge("sales_dlq_messages", "Messages in the DLQ by error reason", ["reason"])

# Jobs
RECONCILIATION_MISMATCHES = Gauge(
    "sales_reconciliation_mismatches", "Store-days where aggregates differ from raw events"
)
