from __future__ import annotations

import logging
from pathlib import Path

import pytest

import portfolio_news.sheets as sheets_module
from portfolio_news.errors import SheetError
from portfolio_news.retrying import retry_call as real_retry_call
from portfolio_news.sheets import SheetsPortfolioReader, normalize_ticker, quote_sheet_title


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload
        self.execute_calls = []

    def execute(self, **kwargs):
        self.execute_calls.append(kwargs)
        return self.payload


class SequenceRequest:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def execute(self, **kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeValues:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    def batchGet(self, **kwargs):
        self.kwargs = kwargs
        return FakeRequest(self.payload)


class FakeSpreadsheets:
    def __init__(self, metadata, values):
        self.metadata = metadata
        self._values = FakeValues(values)
        self.get_kwargs = None

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        return FakeRequest(self.metadata)

    def values(self):
        return self._values


class FakeService:
    def __init__(self, metadata, values):
        self.resource = FakeSpreadsheets(metadata, values)

    def spreadsheets(self):
        return self.resource


def make_service(value_ranges, title="PORTFOLIO_TRACKER", sheets=("overview",)):
    metadata = {
        "properties": {"title": title},
        "sheets": [{"properties": {"title": sheet}} for sheet in sheets],
    }
    return FakeService(metadata, {"valueRanges": value_ranges})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" NASDAQ:goog ", "NASDAQ:GOOG"),
        ("EPA:CW8", "EPA:CW8"),
        ("INDEXSP:.INX", "INDEXSP:.INX"),
        ("BRK.B", "BRK.B"),
        ("bad ticker", None),
        ("=GOOGLEFINANCE(A1)", None),
        (42, None),
    ],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


def test_quote_sheet_title_escapes_apostrophe():
    assert quote_sheet_title("Owner's holdings") == "'Owner''s holdings'"


def test_reads_only_overview_and_returns_unique_valid_tickers(tmp_path, caplog):
    service = make_service(
        [
            {
                "values": [
                    ["Account", "Value"],
                    ["Ticker", "Weight"],
                    [" nasdaq:goog ", 1],
                    ["bad ticker", 2],
                    ["NASDAQ:GOOG", 3],
                    ["EPA:MC", 4],
                ]
            }
        ],
        sheets=("Overview", "transactions"),
    )
    reader = SheetsPortfolioReader("id", tmp_path / "credentials.json", service=service)
    with caplog.at_level(logging.WARNING):
        assert reader.read_tickers() == ["NASDAQ:GOOG", "EPA:MC"]
    assert "malformed_ticker_row" in caplog.text
    assert service.resource._values.kwargs["ranges"] == ["'Overview'"]


def test_ignores_ticker_header_in_other_worksheets(tmp_path):
    service = make_service(
        [{"values": [["Ticker"], ["NASDAQ:GOOG"]]}],
        sheets=("overview", "transactions"),
    )
    reader = SheetsPortfolioReader("id", tmp_path / "credentials.json", service=service)
    assert reader.read_tickers() == ["NASDAQ:GOOG"]
    assert service.resource._values.kwargs["ranges"] == ["'overview'"]


def test_fails_if_no_header(tmp_path):
    reader = SheetsPortfolioReader(
        "id",
        Path("unused"),
        service=make_service([{"values": [["Symbol"], ["NASDAQ:GOOG"]]}]),
    )
    with pytest.raises(SheetError, match="No case-insensitive"):
        reader.read_tickers()


def test_fails_if_multiple_headers(tmp_path):
    service = make_service(
        [{"values": [["Ticker", "ticker"]]}],
    )
    reader = SheetsPortfolioReader("id", Path("unused"), service=service)
    with pytest.raises(SheetError, match="Multiple 'Ticker'.*overview"):
        reader.read_tickers()


def test_fails_if_overview_worksheet_is_missing(tmp_path):
    service = make_service(
        [{"values": [["Ticker"], ["NASDAQ:GOOG"]]}],
        sheets=("transactions",),
    )
    reader = SheetsPortfolioReader("id", Path("unused"), service=service)
    with pytest.raises(SheetError, match="Required worksheet 'overview'.*transactions"):
        reader.read_tickers()


def test_fails_on_wrong_workbook_title():
    service = make_service([], title="Wrong")
    reader = SheetsPortfolioReader("id", Path("unused"), service=service)
    with pytest.raises(SheetError, match="PORTFOLIO_TRACKER"):
        reader.read_tickers()


def test_sheets_timeout_is_retried_without_google_builtin_retries(monkeypatch):
    monkeypatch.setattr(
        sheets_module,
        "retry_call",
        lambda function, **kwargs: real_retry_call(function, sleep=lambda _: None, **kwargs),
    )
    request = SequenceRequest([TimeoutError("slow"), {"ok": True}])
    reader = SheetsPortfolioReader("id", Path("unused"), attempts=2, service=object())
    assert reader._execute(request, "test") == {"ok": True}
    assert request.calls == 2
