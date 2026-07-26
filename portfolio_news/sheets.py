"""Read and validate portfolio tickers through the Google Sheets API."""

from __future__ import annotations

from itertools import zip_longest
import logging
from pathlib import Path
import re
from typing import Any, Optional

from .errors import SheetError
from .retrying import retry_call


READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
EXPECTED_WORKBOOK_TITLE = "PORTFOLIO_TRACKER"
_TICKER_PATTERN = re.compile(
    r"^(?:[A-Z0-9][A-Z0-9._-]{0,31}:)?[A-Z0-9.][A-Z0-9._-]{0,31}$"
)


def normalize_ticker(raw: object) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    value = raw.strip().upper()
    if not value or value == "TICKER" or not _TICKER_PATTERN.fullmatch(value):
        return None
    return value


def quote_sheet_title(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


class SheetsPortfolioReader:
    def __init__(
        self,
        spreadsheet_id: str,
        credentials_file: Path,
        *,
        timeout_seconds: float = 30,
        attempts: int = 3,
        service: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.credentials_file = credentials_file
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._service = service
        self.log = logger or logging.getLogger(__name__)

    def _build_service(self) -> Any:
        try:
            import httplib2
            from google.oauth2 import service_account
            from google_auth_httplib2 import AuthorizedHttp
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise SheetError("Google Sheets dependencies are not installed") from exc

        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(self.credentials_file), scopes=[READONLY_SCOPE]
            )
            http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=self.timeout_seconds))
            return build("sheets", "v4", http=http, cache_discovery=False)
        except Exception as exc:
            raise SheetError("Could not initialize Google Sheets read-only credentials") from exc

    @property
    def service(self) -> Any:
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _execute(self, request: Any, operation: str) -> dict[str, Any]:
        try:
            return retry_call(
                lambda: request.execute(num_retries=0),
                operation=operation,
                attempts=self.attempts,
                logger=self.log,
            )
        except SheetError:
            raise
        except Exception as exc:
            raise SheetError(f"Google Sheets operation failed: {operation}") from exc

    def read_tickers(self) -> list[str]:
        metadata_request = self.service.spreadsheets().get(
            spreadsheetId=self.spreadsheet_id,
            fields="properties.title,sheets.properties.title",
        )
        metadata = self._execute(metadata_request, "sheets_metadata")
        actual_title = str(metadata.get("properties", {}).get("title", ""))
        if actual_title != EXPECTED_WORKBOOK_TITLE:
            raise SheetError(
                f"Spreadsheet title must be {EXPECTED_WORKBOOK_TITLE!r}; found {actual_title!r}"
            )

        worksheet_titles = [
            str(sheet.get("properties", {}).get("title", ""))
            for sheet in metadata.get("sheets", [])
            if sheet.get("properties", {}).get("title")
        ]
        if not worksheet_titles:
            raise SheetError("Spreadsheet has no worksheets")

        ranges = [quote_sheet_title(title) for title in worksheet_titles]
        values_request = self.service.spreadsheets().values().batchGet(
            spreadsheetId=self.spreadsheet_id,
            ranges=ranges,
            majorDimension="ROWS",
        )
        response = self._execute(values_request, "sheets_values")
        value_ranges = response.get("valueRanges", [])

        headers: list[tuple[str, int, int, list[list[object]]]] = []
        for title, value_range in zip_longest(worksheet_titles, value_ranges, fillvalue={}):
            if not isinstance(title, str):
                continue
            rows = value_range.get("values", []) if isinstance(value_range, dict) else []
            for row_index, row in enumerate(rows, start=1):
                if not isinstance(row, list):
                    continue
                for column_index, cell in enumerate(row):
                    if isinstance(cell, str) and cell.strip().casefold() == "ticker":
                        headers.append((title, row_index, column_index, rows))

        if not headers:
            raise SheetError("No case-insensitive 'Ticker' header found in any worksheet")
        if len(headers) > 1:
            locations = ", ".join(
                f"{title}!R{row}C{column + 1}" for title, row, column, _ in headers
            )
            raise SheetError(f"Multiple 'Ticker' headers found: {locations}")

        title, header_row, column, rows = headers[0]
        tickers: list[str] = []
        seen: set[str] = set()
        for row_number, row in enumerate(rows[header_row:], start=header_row + 1):
            if column >= len(row):
                continue
            raw = row[column]
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            ticker = normalize_ticker(raw)
            if ticker is None:
                self.log.warning(
                    "malformed_ticker_row",
                    extra={"worksheet": title, "row": row_number, "value": str(raw)[:120]},
                )
                continue
            if ticker not in seen:
                tickers.append(ticker)
                seen.add(ticker)

        if not tickers:
            raise SheetError(f"No valid tickers found below {title}!R{header_row}C{column + 1}")
        self.log.info(
            "portfolio_loaded",
            extra={"worksheet": title, "ticker_count": len(tickers)},
        )
        return tickers
