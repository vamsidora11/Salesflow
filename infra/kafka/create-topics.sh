#!/usr/bin/env bash
# Creates the platform topics (idempotent) and prints their configuration.
# Run by the kafka-init service; see SALES_ANALYTICS_PLAN.md section 5.
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
KAFKA_BIN="/opt/kafka/bin"

create_topic() {
  local name="$1" partitions="$2" retention_ms="$3"
  "${KAFKA_BIN}/kafka-topics.sh" --bootstrap-server "${BOOTSTRAP}" \
    --create --if-not-exists \
    --topic "${name}" \
    --partitions "${partitions}" \
    --replication-factor 1 \
    --config "retention.ms=${retention_ms}"
}

# 7 days
create_topic "sales.events.v1" 6 604800000
# 30 days
create_topic "sales.events.dlq.v1" 3 2592000000

"${KAFKA_BIN}/kafka-topics.sh" --bootstrap-server "${BOOTSTRAP}" --describe \
  --topic "sales.events.v1"
"${KAFKA_BIN}/kafka-topics.sh" --bootstrap-server "${BOOTSTRAP}" --describe \
  --topic "sales.events.dlq.v1"
