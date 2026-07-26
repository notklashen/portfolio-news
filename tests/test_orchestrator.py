from __future__ import annotations

from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from portfolio_news.errors import TelegramError
from portfolio_news.history import HistoryStore
from portfolio_news.models import ResearchDigest
from portfolio_news.orchestrator import PortfolioNewsOrchestrator, calculate_lookback_start
from portfolio_news.research import ResearchResult


PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 27, 8, tzinfo=PARIS)  # Monday


class FakeSheets:
    def __init__(self):
        self.calls = 0

    def read_tickers(self):
        self.calls += 1
        return ["NASDAQ:GOOG"]


class FakeResearcher:
    def __init__(self, digests):
        self.digests = list(digests)
        self.calls = []

    def research(self, tickers, **kwargs):
        self.calls.append((tickers, kwargs))
        digest = self.digests.pop(0) if len(self.digests) > 1 else self.digests[0]
        return ResearchResult(
            digest=digest,
            response_id=f"resp_{len(self.calls)}",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            web_search_calls=1,
            source_metadata=[],
        )


class FakeTelegram:
    def __init__(self, failures=0):
        self.failures = failures
        self.messages = []

    def send(self, rendered):
        self.messages.append(rendered)
        if self.failures:
            self.failures -= 1
            raise TelegramError("temporary Telegram failure")
        return 100 + len(self.messages)


def make_orchestrator(settings, digest_sequence, telegram=None):
    history = HistoryStore(settings.database_path)
    sheets = FakeSheets()
    researcher = FakeResearcher(digest_sequence)
    telegram = telegram or FakeTelegram()
    orchestrator = PortfolioNewsOrchestrator(
        settings,
        history=history,
        sheets=sheets,
        researcher=researcher,
        telegram=telegram,
        now_provider=lambda: NOW,
    )
    return orchestrator, history, sheets, researcher, telegram


def test_success_persists_delivery_and_usage(settings_factory, story_factory):
    settings = settings_factory()
    digest = ResearchDigest(stories=[story_factory(publication_date=NOW.date())])
    orchestrator, history, sheets, researcher, telegram = make_orchestrator(
        settings, [digest]
    )
    outcome = orchestrator.run()
    assert outcome.status == "success"
    assert outcome.telegram_message_id == 101
    assert history.delivered_story_count() == 1
    assert history.pending_prepared_digest() is None
    assert sheets.calls == 1
    assert len(researcher.calls) == 1
    assert len(telegram.messages) == 1


def test_empty_news_sends_heartbeat(settings_factory):
    orchestrator, history, _, _, telegram = make_orchestrator(
        settings_factory(), [ResearchDigest()]
    )
    outcome = orchestrator.run()
    assert outcome.story_count == 0
    assert "No material new portfolio news today" in telegram.messages[0]
    assert history.delivered_story_count() == 0
    assert history.last_successful_coverage_end() == NOW


def test_telegram_failure_reuses_prepared_digest_without_research(
    settings_factory, story_factory
):
    telegram = FakeTelegram(failures=1)
    digest = ResearchDigest(stories=[story_factory(publication_date=NOW.date())])
    orchestrator, history, sheets, researcher, _ = make_orchestrator(
        settings_factory(), [digest], telegram=telegram
    )
    with pytest.raises(TelegramError):
        orchestrator.run()
    pending = history.pending_prepared_digest()
    assert pending is not None
    assert history.delivered_story_count() == 0

    outcome = orchestrator.run()
    assert outcome.reused_prepared_digest is True
    assert outcome.rendered_html == pending.rendered_html
    assert sheets.calls == 1
    assert len(researcher.calls) == 1
    assert history.delivered_story_count() == 1
    connection = sqlite3.connect(settings_factory().database_path)
    try:
        usage_rows = connection.execute(
            "SELECT status, total_tokens, web_search_calls FROM runs ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    assert usage_rows == [("failed", 120, 1), ("success", 0, 0)]


def test_successful_rerun_does_not_redeliver_same_url(
    settings_factory, story_factory
):
    story = story_factory(publication_date=NOW.date())
    digest = ResearchDigest(stories=[story])
    orchestrator, history, _, _, telegram = make_orchestrator(
        settings_factory(), [digest, digest]
    )
    orchestrator.run()
    second = orchestrator.run()
    assert second.story_count == 0
    assert "No material new portfolio news today" in telegram.messages[1]
    assert history.delivered_story_count() == 1


def test_same_event_different_publisher_requires_material_update(
    settings_factory, story_factory
):
    first = story_factory(publication_date=NOW.date())
    repeated = story_factory(
        publisher="Associated Press",
        url="https://apnews.com/article/another-publisher-same-event",
        relevance_summary="A differently worded but unmarked follow-up.",
        publication_date=NOW.date(),
    )
    update = story_factory(
        url="https://reuters.com/technology/material-update",
        relevance_summary="The regulator has now imposed a binding remedy that changes revenue risk.",
        material_update=True,
        publication_date=NOW.date(),
    )
    orchestrator, history, _, researcher, _ = make_orchestrator(
        settings_factory(),
        [
            ResearchDigest(stories=[first]),
            ResearchDigest(stories=[repeated]),
            ResearchDigest(stories=[update]),
        ],
    )
    orchestrator.run()
    assert orchestrator.run().story_count == 0
    assert orchestrator.run().story_count == 1
    assert history.delivered_story_count() == 2
    assert len(researcher.calls) == 3


def test_material_update_flag_without_changed_summary_is_suppressed(
    settings_factory, story_factory
):
    first = story_factory(publication_date=NOW.date())
    unchanged = story_factory(
        url="https://reuters.com/technology/new-url-same-facts",
        material_update=True,
        publication_date=NOW.date(),
    )
    orchestrator, history, _, _, _ = make_orchestrator(
        settings_factory(),
        [ResearchDigest(stories=[first]), ResearchDigest(stories=[unchanged])],
    )
    orchestrator.run()
    assert orchestrator.run().story_count == 0
    assert history.delivered_story_count() == 1


def test_dry_run_never_prepares_or_delivers(settings_factory, story_factory):
    digest = ResearchDigest(stories=[story_factory(publication_date=NOW.date())])
    orchestrator, history, _, _, telegram = make_orchestrator(settings_factory(), [digest])
    outcome = orchestrator.run(dry_run=True)
    assert outcome.status == "dry_run"
    assert telegram.messages == []
    assert history.pending_prepared_digest() is None
    assert history.delivered_story_count() == 0


def test_monday_lookback_reaches_friday_and_caps_at_four_days():
    friday = datetime(2026, 7, 24, 8, tzinfo=PARIS)
    thursday = datetime(2026, 7, 23, 1, tzinfo=PARIS)
    assert calculate_lookback_start(NOW, friday) == friday
    assert calculate_lookback_start(NOW, thursday) == datetime(2026, 7, 23, 8, tzinfo=PARIS)
