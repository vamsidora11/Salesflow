import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, request_id_var, sanitize_request_id, tenant_id_var
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_openapi_operations_are_documented_and_stable(app: FastAPI) -> None:
    schema = app.openapi()
    seen: set[str] = set()
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            assert op.get("summary"), f"{method} {path} has no summary"
            assert op.get("tags"), f"{method} {path} has no tag"
            op_id = op["operationId"]
            assert op_id == f"{op['tags'][0]}_{op_id.split('_', 1)[1]}"
            assert op_id not in seen, f"duplicate operationId {op_id}"
            seen.add(op_id)
    assert "ingest_ingest_events" in seen
    assert "/api/v1/ws/dashboard" not in schema["paths"]  # documented in docs/websocket.md


def test_error_responses_are_documented(app: FastAPI) -> None:
    paths = app.openapi()["paths"]
    ingest = paths["/api/v1/ingest/events"]["post"]["responses"]
    assert {"202", "401", "413", "429", "503"} <= set(ingest)
    assert "503" in paths["/health/ready"]["get"]["responses"]
    assert "404" in paths["/api/v1/exports/{job_id}"]["get"]["responses"]


def test_committed_openapi_json_matches_the_app() -> None:
    """Same check CI runs; regenerate with `python scripts/export_openapi.py`."""
    committed = json.loads((REPO_ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(create_app().openapi(), sort_keys=True))


def test_request_id_is_echoed_and_unsafe_ids_are_replaced(client: TestClient) -> None:
    ok = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert ok.headers["X-Request-ID"] == "trace-123"
    unsafe = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert unsafe.headers["X-Request-ID"] != "bad id\nwith newline"
    assert sanitize_request_id(None)
    assert sanitize_request_id("x" * 200) != "x" * 200


def test_json_logs_carry_request_and_tenant_ids() -> None:
    request_id_var.set("req-abc")
    tenant_id_var.set("tenant-1")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.extra_field = 7
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-abc"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["extra_field"] == 7
    request_id_var.set(None)
    tenant_id_var.set(None)


def test_metrics_endpoint_exposes_prometheus_text(client: TestClient) -> None:
    body = client.get("/metrics").text
    assert "sales_ingest_events_total" in body
    assert "/metrics" not in client.app.openapi()["paths"]  # type: ignore[attr-defined]
