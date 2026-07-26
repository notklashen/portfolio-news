from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import portfolio_news.telegram as telegram_module
from portfolio_news.errors import TelegramError, TelegramRenderError
from portfolio_news.models import ResearchDigest, StoryCategory
from portfolio_news.telegram import TelegramClient, render_digest
from portfolio_news.retrying import retry_call as real_retry_call


NOW = datetime(2026, 7, 26, 8, tzinfo=ZoneInfo("Europe/Paris"))


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json):
        self.calls.append((url, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_render_escapes_user_controlled_html_and_keeps_safe_link(story_factory):
    story = story_factory(
        affected_tickers=["<GOOG>&"],
        headline="Earnings <beat> & outlook",
        relevance_summary="A <tag> & material impact.",
        publisher="Reuters & Co",
        url="https://reuters.com/item?a=1&b=2",
    )
    rendered = render_digest(ResearchDigest(stories=[story]), when=NOW)
    assert "&lt;GOOG&gt;&amp;" in rendered
    assert "Earnings &lt;beat&gt; &amp; outlook" in rendered
    assert 'href="https://reuters.com/item?a=1&amp;b=2"' in rendered
    assert len(rendered) <= 3500


def test_empty_digest_renders_heartbeat():
    rendered = render_digest(ResearchDigest(), when=NOW)
    assert "No material new portfolio news today" in rendered
    assert "26 July 2026" in rendered


def test_render_enforces_configured_length_without_slicing_html(story_factory):
    stories = []
    for index in range(7):
        stories.append(
            story_factory(
                category=(
                    StoryCategory.PORTFOLIO
                    if index < 5
                    else StoryCategory.MACRO_GEOPOLITICAL
                ),
                url=f"https://reuters.com/item-{index}",
                event_key=f"event-{index}",
                headline=f"Headline {index} " + "H" * 180,
                relevance_summary="R" * 600,
            )
        )
    rendered = render_digest(ResearchDigest(stories=stories), when=NOW, max_chars=900)
    assert len(rendered) <= 900
    assert rendered.count("<a href=") == rendered.count("</a>")
    assert rendered.count("<b>") == rendered.count("</b>")


def test_tiny_limit_fails_cleanly():
    with pytest.raises(TelegramRenderError):
        render_digest(ResearchDigest(), when=NOW, max_chars=20)


def test_telegram_send_uses_html_and_returns_message_id():
    http = FakeHTTPClient([FakeResponse(200, {"ok": True, "result": {"message_id": 99}})])
    client = TelegramClient("token", "chat", attempts=1, http_client=http)
    assert client.send("<b>Hello</b>") == 99
    _, payload = http.calls[0]
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"] == {"is_disabled": True}


def test_telegram_failure_redacts_token():
    token = "123456:telegram-secret-token-value"
    http = FakeHTTPClient([RuntimeError(f"request to /bot{token}/sendMessage failed")])
    client = TelegramClient(token, "chat", attempts=1, http_client=http)
    with pytest.raises(TelegramError) as error:
        client.send("hello")
    assert token not in str(error.value)


def test_telegram_transport_and_rate_limit_are_retried(monkeypatch):
    monkeypatch.setattr(
        telegram_module,
        "retry_call",
        lambda function, **kwargs: real_retry_call(function, sleep=lambda _: None, **kwargs),
    )
    http = FakeHTTPClient(
        [
            TimeoutError("slow"),
            FakeResponse(429, {"ok": False, "error_code": 429, "description": "retry"}),
            FakeResponse(200, {"ok": True, "result": {"message_id": 12}}),
        ]
    )
    client = TelegramClient("token", "chat", attempts=3, http_client=http)
    assert client.send("hello") == 12
    assert len(http.calls) == 3
