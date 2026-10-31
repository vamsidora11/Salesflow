"""CSV and XLSX serialisation of export rows. Pure functions: no I/O to the database."""

import csv
import io
from collections.abc import AsyncIterable, AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import xlsxwriter

from app.repositories.exports import ExportRow

HEADERS = [
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
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: str) -> str:
    """Neutralise spreadsheet formula injection from tenant-controlled names."""
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def format_bucket(bucket: datetime, granularity: Literal["hour", "day"]) -> str:
    return bucket.date().isoformat() if granularity == "day" else bucket.isoformat()


def _csv_values(row: ExportRow, granularity: Literal["hour", "day"]) -> list[Any]:
    return [
        format_bucket(row.bucket, granularity),
        safe_cell(row.store),
        safe_cell(row.region),
        safe_cell(row.sku),
        safe_cell(row.product),
        row.channel,
        f"{row.revenue:.2f}",
        row.units,
        row.transactions,
    ]


async def csv_chunks(
    rows: AsyncIterable[ExportRow],
    granularity: Literal["hour", "day"],
    rows_per_chunk: int = 1000,
) -> AsyncIterator[str]:
    """Yield CSV text in chunks so a StreamingResponse never holds the whole file."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADERS)
    pending = 0
    async for row in rows:
        writer.writerow(_csv_values(row, granularity))
        pending += 1
        if pending >= rows_per_chunk:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            pending = 0
    if buffer.tell():
        yield buffer.getvalue()


class XlsxExportWriter:
    """Sheet 1: Summary (KPIs and filters). Sheet 2+: Data, split at Excel's row limit.

    Uses XlsxWriter's constant-memory mode, so rows must be written in order.
    """

    def __init__(
        self,
        path: Path,
        granularity: Literal["hour", "day"],
        summary: dict[str, Any],
        max_rows_per_sheet: int = 1_048_576,
    ) -> None:
        self._granularity = granularity
        self._max_data_rows = max_rows_per_sheet - 1  # header occupies the first row
        self._wb = xlsxwriter.Workbook(str(path), {"constant_memory": True})
        self._money = self._wb.add_format({"num_format": "0.00"})
        self._int = self._wb.add_format({"num_format": "0"})
        self._date = self._wb.add_format(
            {"num_format": "yyyy-mm-dd" if granularity == "day" else "yyyy-mm-dd hh:mm"}
        )
        self._header = self._wb.add_format({"bold": True})
        self._sheet: Any = None
        self._sheet_rows = 0
        self._sheet_count = 0
        self.rows_written = 0
        self._write_summary(summary)
        self._new_data_sheet()

    def _write_summary(self, summary: dict[str, Any]) -> None:
        sheet = self._wb.add_worksheet("Summary")
        sheet.write_row(0, 0, ["Metric", "Value"], self._header)
        for i, (key, value) in enumerate(summary.items(), start=1):
            sheet.write(i, 0, key)
            if isinstance(value, bool) or value is None or isinstance(value, str):
                sheet.write(i, 1, "" if value is None else str(value))
            else:
                sheet.write_number(i, 1, float(value))
        sheet.set_column(0, 0, 28)
        sheet.set_column(1, 1, 36)

    def _new_data_sheet(self) -> None:
        self._finish_data_sheet()
        self._sheet_count += 1
        name = "Data" if self._sheet_count == 1 else f"Data {self._sheet_count}"
        self._sheet = self._wb.add_worksheet(name)
        self._sheet.write_row(0, 0, HEADERS, self._header)
        self._sheet.freeze_panes(1, 0)
        self._sheet.set_column(0, 0, 18)
        self._sheet.set_column(1, 4, 22)
        self._sheet_rows = 0

    def _finish_data_sheet(self) -> None:
        if self._sheet is not None:
            self._sheet.autofilter(0, 0, max(self._sheet_rows, 1), len(HEADERS) - 1)

    def write(self, row: ExportRow) -> None:
        if self._sheet_rows >= self._max_data_rows:
            self._new_data_sheet()
        r = self._sheet_rows + 1
        self._sheet_rows += 1
        self.rows_written += 1
        s = self._sheet
        s.write_datetime(r, 0, row.bucket.replace(tzinfo=None), self._date)
        s.write_string(r, 1, safe_cell(row.store))
        s.write_string(r, 2, safe_cell(row.region))
        s.write_string(r, 3, safe_cell(row.sku))
        s.write_string(r, 4, safe_cell(row.product))
        s.write_string(r, 5, row.channel)
        s.write_number(r, 6, float(row.revenue), self._money)
        s.write_number(r, 7, row.units, self._int)
        s.write_number(r, 8, row.transactions, self._int)

    def close(self) -> None:
        self._finish_data_sheet()
        self._wb.close()
