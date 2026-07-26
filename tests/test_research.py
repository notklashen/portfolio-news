from __future__ import annotations

from datetime import datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from openai import OpenAI
from pydantic import ValidationError

import portfolio_news.research as research_module
from portfolio_news.errors import ResearchError
from portfolio_news.models import ResearchDigest
from portfolio_news.research import OpenAIResearcher
from portfolio_news.retrying import retry_call as real_retry_call


PARIS = ZoneInfo("Europe/Paris")
START = datetime(2026, 7, 25, 8, tzinfo=PARIS)
END = datetime(2026, 7, 26, 8, tzinfo=PARIS)


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class SequenceResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


class FakeStatusError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def response_for(digest, source_url):
    urls = source_url if isinstance(source_url, list) else [source_url]
    return SimpleNamespace(
        output_parsed=digest,
        output_text="",
        id="resp_123",
        usage=SimpleNamespace(input_tokens=120, output_tokens=30, total_tokens=150),
        output=[
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {"type": "url", "title": "Source", "url": url, "extra": "kept"}
                        for url in urls
                    ]
                },
            }
        ],
    )


def test_uses_one_structured_unrestricted_responses_request(story_factory):
    story = story_factory(publication_date=END.date())
    response = response_for(ResearchDigest(stories=[story]), story.url)
    client = FakeClient(response)
    researcher = OpenAIResearcher(
        "key",
        model="gpt-5.6-sol",
        client=client,
    )
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]
    assert result.response_id == "resp_123"
    assert result.total_tokens == 150
    assert result.web_search_calls == 1
    assert result.source_metadata[0]["extra"] == "kept"
    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.6-sol"
    assert call["reasoning"] == {"effort": "medium"}
    assert call["tools"][0]["type"] == "web_search"
    assert call["tools"][0]["search_context_size"] == "medium"
    assert "filters" not in call["tools"][0]
    assert call["include"] == ["web_search_call.action.sources"]
    assert call["text_format"] is ResearchDigest
    assert call["text"] == {"verbosity": "low"}
    assert call["max_output_tokens"] == 10000
    assert call["store"] is False
    assert "Mention every supplied holding" in call["input"][0]["content"]
    assert "NASDAQ:GOOG" in call["input"][1]["content"]
    assert "Recap cutoff" in call["input"][1]["content"]
    assert '"exchange_name":"Nasdaq"' in call["input"][1]["content"]


def test_pinned_sdk_serializes_strict_schema_and_web_search_contract():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        payload = {
            "id": "resp_mock",
            "created_at": 0,
            "model": "gpt-5.6-sol",
            "object": "response",
            "output": [
                {
                    "id": "msg_1",
                    "content": [
                        {
                            "annotations": [],
                            "text": json.dumps({"stories": []}),
                            "type": "output_text",
                        }
                    ],
                    "role": "assistant",
                    "status": "completed",
                    "type": "message",
                }
            ],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
        }
        return httpx.Response(200, json=payload, request=request)

    client = OpenAI(
        api_key="test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = client.responses.parse(
        model="gpt-5.6-sol",
        reasoning={"effort": "medium"},
        tools=[
            {
                "type": "web_search",
                "search_context_size": "medium",
            }
        ],
        include=["web_search_call.action.sources"],
        input="test",
        text_format=ResearchDigest,
        text={"verbosity": "low"},
        store=False,
    )
    assert response.output_parsed == ResearchDigest()
    assert captured["body"]["tools"][0] == {
        "type": "web_search",
        "search_context_size": "medium",
    }
    assert captured["body"]["text"]["format"]["strict"] is True
    assert captured["body"]["text"]["verbosity"] == "low"


def test_accepts_consulted_source_outside_legacy_allowlist(story_factory):
    story = story_factory(
        url="https://untrusted.example/news/item", publication_date=END.date()
    )
    client = FakeClient(response_for(ResearchDigest(stories=[story]), story.url))
    researcher = OpenAIResearcher("key", client=client)
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]


def test_does_not_reject_citation_absent_from_consulted_sources(story_factory):
    story = story_factory(publication_date=END.date())
    response = response_for(
        ResearchDigest(stories=[story]), "https://www.reuters.com/different-story"
    )
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]


def test_malformed_structured_output_fails_clearly():
    response = SimpleNamespace(
        output_parsed=None,
        output_text="not-json",
        output=[],
        usage=None,
        id="resp_bad",
    )
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    with pytest.raises(ResearchError, match="malformed structured"):
        researcher.research(
            ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
        )


def test_sdk_validation_error_is_reported_as_malformed_output():
    try:
        ResearchDigest.model_validate({"stories": "not-a-list"})
    except Exception as validation_error:
        researcher = OpenAIResearcher("key", client=FakeClient(validation_error))
    with pytest.raises(ResearchError, match="malformed structured"):
        researcher.research(
            ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
        )


def test_truncated_structured_output_is_reported_clearly():
    try:
        ResearchDigest.model_validate_json('{"stories":[{"headline":"cut off')
    except ValidationError as validation_error:
        researcher = OpenAIResearcher("key", client=FakeClient(validation_error))
    with pytest.raises(ResearchError, match="truncated before completing its JSON"):
        researcher.research(
            ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
        )


def test_story_without_consulted_source_is_accepted(story_factory):
    story = story_factory(publication_date=END.date())
    response = SimpleNamespace(
        output_parsed=ResearchDigest(stories=[story]),
        output_text="",
        id="resp_no_search",
        usage=None,
        output=[],
    )
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]


def test_unknown_ticker_is_not_post_validated(story_factory):
    story = story_factory(affected_tickers=["NASDAQ:MSFT"], publication_date=END.date())
    response = response_for(ResearchDigest(stories=[story]), story.url)
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]


def test_allows_context_source_outside_daily_window(story_factory):
    story = story_factory(publication_date=START.date().replace(day=24))
    response = response_for(ResearchDigest(stories=[story]), story.url)
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]


def test_missing_holding_coverage_is_not_post_validated(story_factory):
    story = story_factory(publication_date=END.date())
    response = response_for(ResearchDigest(stories=[story]), story.url)
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG", "NASDAQ:MSFT"],
        lookback_start=START,
        lookback_end=END,
        recent_history=[],
    )
    assert result.digest.stories == [story]


def test_preserves_all_paragraph_citations(story_factory):
    second_url = "https://finance.example/quote/goog"
    story = story_factory(
        publication_date=END.date(),
        citations=[{"publisher": "Finance Example", "url": second_url}],
    )
    response = response_for(ResearchDigest(stories=[story]), [story.url, second_url])
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert [citation.url for citation in result.digest.stories[0].all_citations] == [
        story.url,
        second_url,
    ]


def test_ticker_resolution_hints_expand_euronext_codes():
    assert OpenAIResearcher._ticker_search_hints(["EPA:MC", "AMS:ASML", "HYPE"]) == [
        {
            "holding": "EPA:MC",
            "symbol": "MC",
            "exchange_code": "EPA",
            "exchange_name": "Euronext Paris",
        },
        {
            "holding": "AMS:ASML",
            "symbol": "ASML",
            "exchange_code": "AMS",
            "exchange_name": "Euronext Amsterdam",
        },
        {"holding": "HYPE", "symbol": "HYPE"},
    ]


def test_price_only_paragraph_is_not_post_validated(story_factory):
    story = story_factory(
        headline="Alphabet closed higher.",
        relevance_summary="No fresh, verified catalyst emerged during the research window.",
    )
    response = response_for(ResearchDigest(stories=[story]), story.url)
    researcher = OpenAIResearcher("key", client=FakeClient(response))
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]


def test_openai_rate_limit_is_retried(monkeypatch, story_factory):
    story = story_factory(publication_date=END.date())
    success = response_for(ResearchDigest(stories=[story]), story.url)
    client = SimpleNamespace(responses=SequenceResponses([FakeStatusError(429), success]))
    monkeypatch.setattr(
        research_module,
        "retry_call",
        lambda function, **kwargs: real_retry_call(function, sleep=lambda _: None, **kwargs),
    )
    researcher = OpenAIResearcher("key", attempts=2, client=client)
    result = researcher.research(
        ["NASDAQ:GOOG"], lookback_start=START, lookback_end=END, recent_history=[]
    )
    assert result.digest.stories == [story]
    assert len(client.responses.calls) == 2
