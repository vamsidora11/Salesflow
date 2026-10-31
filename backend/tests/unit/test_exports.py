import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import Settings
from app.db.models import ExportJob
from app.repositories.exports import ExportRow
from app.repositories.filters import AggregateFilters
from app.schemas.exports import ExportFilters
from app.services.export_files import XlsxExportWriter, csv_chunks, safe_cell
from app.services.exports import (
    CsvStreamService,
    ExportExpiredError,
    ExportNotFoundError,
    ExportNotReadyError,
    ExportService,
    ExportTooLargeError,
)
from app.storage.s3 import S3ObjectStore
from tests.conftest import TENANT_A, TENANT_B, auth_headers


def row(n: int = 0, **kw: Any) -> ExportRow:
    return ExportRow(
        bucket=kw.get("bucket", datetime(2026, 10, 30, tzinfo=UTC) + timedelta(days=n)),
        store=kw.get("store", f"Store {n}"),
        region=kw.get("region", "EU"),
        sku=kw.get("sku", f"SKU-{n}"),
        product=kw.get("product", "Widget"),
        channel="pos",
        revenue=kw.get("revenue", Decimal("12.5")),
        units=3,
        transactions=2,
    )


async def agen(rows: list[ExportRow]) -> AsyncIterator[ExportRow]:
    for r in rows:
        yield r


# --- CSV ----------------------------------------------------------------------------------------


async def test_csv_has_header_two_decimal_money_and_day_dates() -> None:
    chunks = [c async for c in csv_chunks(agen([row(0), row(1)]), "day")]
    lines = "".join(chunks).splitlines()
    assert lines[0] == "bucket,store,region,sku,product,channel,revenue,units,transactions"
    assert lines[1] == "2026-10-30,Store 0,EU,SKU-0,Widget,pos,12.50,3,2"
    assert len(lines) == 3


async def test_csv_is_emitted_in_bounded_chunks() -> None:
    rows = [row(i % 20) for i in range(2500)]
    chunks = [c async for c in csv_chunks(agen(rows), "day", rows_per_chunk=1000)]
    assert len(chunks) == 3
    assert "".join(chunks).count("\r\n") == 2501


@pytest.mark.parametrize("danger", ["=1+1", "+cmd", "-2", "@SUM(A1)"])
async def test_csv_neutralises_formula_injection(danger: str) -> None:
    text = "".join([c async for c in csv_chunks(agen([row(0, store=danger)]), "day")])
    assert f",'{danger}," in text
    assert safe_cell("plain") == "plain"


async def test_csv_hourly_uses_full_timestamps() -> None:
    r = row(0, bucket=datetime(2026, 10, 30, 14, tzinfo=UTC))
    text = "".join([c async for c in csv_chunks(agen([r]), "hour")])
    assert "2026-10-30T14:00:00+00:00" in text


# --- XLSX ---------------------------------------------------------------------------------------


def write_xlsx(path: Path, rows: list[ExportRow], max_rows: int = 1_048_576) -> None:
    summary = {"Revenue": Decimal("125.50"), "Units": 30, "Granularity": "day", "Store": "all"}
    writer = XlsxExportWriter(path, "day", summary, max_rows)
    for r in rows:
        writer.write(r)
    writer.close()


def test_xlsx_has_summary_and_data_sheets_with_types_and_frozen_header(tmp_path: Path) -> None:
    path = tmp_path / "out.xlsx"
    write_xlsx(path, [row(0), row(1), row(2)])
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Data"]

    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows(min_row=2)}
    assert summary["Revenue"] == 125.5 and summary["Units"] == 30 and summary["Store"] == "all"

    data = wb["Data"]
    assert data.freeze_panes == "A2"
    assert data.auto_filter.ref == "A1:I4"
    assert [c.value for c in data[1]] == [
        "bucket",
        "store",
        "region",
        "sku",
        "product",
        "channel",
        "revenue",
        "units",
        "transactions",
    ]
    first = [c for c in data[2]]
    assert isinstance(first[0].value, datetime) and first[0].number_format == "yyyy-mm-dd"
    assert first[6].value == 12.5 and first[6].number_format == "0.00"  # numbers, not text
    assert first[7].value == 3 and isinstance(first[7].value, int)


def test_xlsx_splits_into_additional_sheets_at_the_row_limit(tmp_path: Path) -> None:
    path = tmp_path / "split.xlsx"
    write_xlsx(path, [row(i) for i in range(7)], max_rows=4)  # header + 3 data rows per sheet
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Data", "Data 2", "Data 3"]
    counts = [wb[n].max_row - 1 for n in ("Data", "Data 2", "Data 3")]
    assert counts == [3, 3, 1]
    assert all(wb[n].freeze_panes == "A2" for n in ("Data", "Data 2", "Data 3"))


def test_xlsx_neutralises_formula_injection(tmp_path: Path) -> None:
    path = tmp_path / "inj.xlsx"
    write_xlsx(path, [row(0, store='=HYPERLINK("http://evil")')])
    cell = openpyxl.load_workbook(path)["Data"]["B2"]
    assert cell.data_type == "s" and cell.value.startswith("'=")


# --- ExportService: job lifecycle, presigned URLs, tenant isolation -----------------------------


class FakeJobs:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, ExportJob] = {}

    async def create(
        self, tenant_id: uuid.UUID, requested_by: uuid.UUID, fmt: str, filters: dict[str, Any]
    ) -> ExportJob:
        job = ExportJob(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            requested_by=requested_by,
            format=fmt,
            filters=filters,
            status="queued",
            created_at=datetime.now(UTC),
        )
        self.rows[job.id] = job
        return job

    async def get_for_tenant(self, tenant_id: uuid.UUID, job_id: uuid.UUID) -> ExportJob | None:
        job = self.rows.get(job_id)
        return job if job and job.tenant_id == tenant_id else None


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    def enqueue(self, job_id: uuid.UUID) -> None:
        self.enqueued.append(job_id)


class FakeStorage:
    def __init__(self) -> None:
        self.urls: list[tuple[str, int]] = []

    def presigned_get_url(self, key: str, expires_seconds: int, filename: str) -> str:
        self.urls.append((key, expires_seconds))
        return f"https://storage.example/{key}?X-Amz-Expires={expires_seconds}"


FILTERS = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-08T00:00:00Z"}


@pytest.fixture
def stack(client: TestClient) -> tuple[FakeJobs, FakeQueue, FakeStorage]:
    jobs, queue, storage = FakeJobs(), FakeQueue(), FakeStorage()
    settings = client.app.state.settings  # type: ignore[attr-defined]
    client.app.dependency_overrides[deps.get_export_service] = lambda: ExportService(  # type: ignore[attr-defined]
        jobs,
        queue,
        storage,
        settings,  # type: ignore[arg-type]
    )
    return jobs, queue, storage


def test_create_export_queues_a_job_for_the_requester(client: TestClient, stack: Any) -> None:
    jobs, queue, _ = stack
    response = client.post(
        "/api/v1/exports", json={"format": "xlsx", "filters": FILTERS}, headers=auth_headers()
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued" and body["format"] == "xlsx"
    job = jobs.rows[uuid.UUID(body["id"])]
    assert job.tenant_id == TENANT_A
    assert queue.enqueued == [job.id]
    assert job.filters["from"].startswith("2026-10-01")


def test_create_export_validates_range_before_queueing(client: TestClient, stack: Any) -> None:
    _, queue, _ = stack
    bad = {"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}
    response = client.post(
        "/api/v1/exports",
        json={"format": "csv", "filters": {**bad, "granularity": "hour"}},
        headers=auth_headers(),
    )
    assert response.status_code == 422
    assert queue.enqueued == []


def test_other_tenants_job_is_404_for_status_and_download(client: TestClient, stack: Any) -> None:
    created = client.post(
        "/api/v1/exports",
        json={"format": "csv", "filters": FILTERS},
        headers=auth_headers("analyst", TENANT_A),
    ).json()
    for suffix in ("", "/download"):
        r = client.get(
            f"/api/v1/exports/{created['id']}{suffix}", headers=auth_headers("analyst", TENANT_B)
        )
        assert r.status_code == 404
    missing = client.get(f"/api/v1/exports/{uuid.uuid4()}", headers=auth_headers())
    assert missing.status_code == 404


def test_download_lifecycle_not_ready_then_ready_then_expired(
    client: TestClient, stack: Any
) -> None:
    jobs, _, storage = stack
    created = client.post(
        "/api/v1/exports", json={"format": "xlsx", "filters": FILTERS}, headers=auth_headers()
    ).json()
    url = f"/api/v1/exports/{created['id']}/download"
    assert client.get(url, headers=auth_headers()).status_code == 409

    job = jobs.rows[uuid.UUID(created["id"])]
    job.status, job.file_key = "succeeded", "exports/t/j.xlsx"
    job.expires_at = datetime.now(UTC) + timedelta(days=7)
    ok = client.get(url, headers=auth_headers())
    assert ok.status_code == 200
    assert ok.json()["expires_in"] == 900  # 15 minutes
    assert storage.urls == [("exports/t/j.xlsx", 900)]

    job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get(url, headers=auth_headers()).status_code == 410
    job.status = "expired"
    assert client.get(url, headers=auth_headers()).status_code == 410


def test_viewer_cannot_export(client: TestClient, stack: Any) -> None:
    r = client.post(
        "/api/v1/exports",
        json={"format": "csv", "filters": FILTERS},
        headers=auth_headers("viewer"),
    )
    assert r.status_code == 403


def test_presigned_url_expires_in_15_minutes() -> None:
    store = S3ObjectStore(
        Settings(
            s3_endpoint_url="http://localhost:9000", s3_public_endpoint_url="http://localhost:9000"
        )
    )
    url = store.presigned_get_url("exports/t/j.xlsx", 900, "export.xlsx")
    query = parse_qs(urlparse(url).query)
    assert query["X-Amz-Expires"] == ["900"]
    assert "X-Amz-Signature" in query
    assert urlparse(url).netloc == "localhost:9000"


# --- synchronous CSV endpoint -------------------------------------------------------------------


class FakeCsvStreamer(CsvStreamService):
    def __init__(self, rows: int, limit: int = 1_000_000) -> None:
        self._rows = rows
        self._limit = limit

    async def prepare(
        self, tenant_id: uuid.UUID, filters: ExportFilters
    ) -> tuple[AggregateFilters, int]:
        if self._rows > self._limit:
            raise ExportTooLargeError(self._rows, self._limit)
        return AggregateFilters(tenant_id, filters.date_from, filters.date_to), self._rows

    async def stream(self, f: AggregateFilters, filters: ExportFilters) -> AsyncIterator[str]:  # type: ignore[override]
        assert f.tenant_id == TENANT_A
        async for chunk in csv_chunks(agen([row(0), row(1)]), filters.granularity):
            yield chunk


def test_csv_endpoint_streams_a_download(client: TestClient) -> None:
    client.app.dependency_overrides[deps.get_csv_stream_service] = lambda: FakeCsvStreamer(2)  # type: ignore[attr-defined]
    r = client.get("/api/v1/exports/csv", params=FILTERS, headers=auth_headers())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.splitlines()[0].startswith("bucket,store")
    assert len(r.text.splitlines()) == 3


def test_csv_endpoint_rejects_over_one_million_rows(client: TestClient) -> None:
    client.app.dependency_overrides[deps.get_csv_stream_service] = lambda: FakeCsvStreamer(
        1_000_001
    )  # type: ignore[attr-defined]
    r = client.get("/api/v1/exports/csv", params=FILTERS, headers=auth_headers())
    assert r.status_code == 413
    assert "POST /exports" in r.json()["detail"]


def test_service_errors_are_distinct_types() -> None:
    assert not issubclass(ExportNotFoundError, ExportNotReadyError)
    assert not issubclass(ExportExpiredError, ExportNotReadyError)
