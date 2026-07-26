from datetime import datetime
from zoneinfo import ZoneInfo

from portfolio_news.history import HistoryStore
from portfolio_news.models import ResearchDigest


NOW = datetime(2026, 7, 26, 8, tzinfo=ZoneInfo("Europe/Paris"))


def test_prepared_digest_becomes_delivery_history_only_after_success(tmp_path, story_factory):
    history = HistoryStore(tmp_path / "history.db")
    history.initialize()
    story = story_factory()
    digest = ResearchDigest(stories=[story])
    prepared_id = history.create_prepared_digest(
        digest_date=NOW.date().isoformat(),
        lookback_start=NOW,
        lookback_end=NOW,
        rendered_html="<b>Digest</b>",
        digest=digest,
        openai_response_id="resp_1",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        web_search_calls=2,
        source_metadata=[{"url": story.url}],
        created_at=NOW,
    )
    pending = history.pending_prepared_digest()
    assert pending is not None
    assert pending.id == prepared_id
    assert history.delivered_story_count() == 0
    assert not history.url_was_delivered("https://reuters.com/technology/alphabet-update-2026-07-26")

    history.mark_prepared_delivered(prepared_id, telegram_message_id=77, delivered_at=NOW)
    assert history.pending_prepared_digest() is None
    assert history.delivered_story_count() == 1
    assert history.url_was_delivered("https://reuters.com/technology/alphabet-update-2026-07-26")
    assert history.latest_event(story.event_key)["headline"] == story.headline
    assert history.last_successful_coverage_end() == NOW


def test_delivery_marking_is_idempotent(tmp_path, story_factory):
    history = HistoryStore(tmp_path / "history.db")
    history.initialize()
    prepared_id = history.create_prepared_digest(
        digest_date=NOW.date().isoformat(),
        lookback_start=NOW,
        lookback_end=NOW,
        rendered_html="digest",
        digest=ResearchDigest(stories=[story_factory()]),
        openai_response_id=None,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        web_search_calls=0,
        source_metadata=[],
        created_at=NOW,
    )
    history.mark_prepared_delivered(prepared_id, telegram_message_id=1, delivered_at=NOW)
    history.mark_prepared_delivered(prepared_id, telegram_message_id=1, delivered_at=NOW)
    assert history.delivered_story_count() == 1

