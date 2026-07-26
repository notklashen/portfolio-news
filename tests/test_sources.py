from datetime import date

import pytest

from portfolio_news.models import DigestStory
from portfolio_news.sources import canonicalize_url, is_url_allowed, normalize_domain


def test_source_allowlist_matches_host_or_subdomain_only():
    assert is_url_allowed("https://www.reuters.com/world/item", ("reuters.com",))
    assert not is_url_allowed("https://reuters.com.evil.example/item", ("reuters.com",))
    assert not is_url_allowed("http://reuters.com/item", ("reuters.com",))
    assert not is_url_allowed("https://user:password@reuters.com/item", ("reuters.com",))


def test_url_canonicalization_removes_tracking_and_fragment():
    url = "https://WWW.Reuters.com:443/world/item/?utm_source=x&b=2&a=1#section"
    assert canonicalize_url(url) == "https://reuters.com/world/item?a=1&b=2"


def test_normalize_domain_accepts_origin_but_not_path():
    assert normalize_domain("https://WWW.Example.com/") == "example.com"
    with pytest.raises(ValueError):
        normalize_domain("https://example.com/news")
    with pytest.raises(ValueError):
        normalize_domain("*.example.com")


def test_event_key_is_stably_normalized(story_factory):
    story: DigestStory = story_factory(event_key=" Alphabet / Q2 Results 2026 ")
    assert story.event_key == "alphabet-q2-results-2026"


def test_story_rejects_non_https(story_factory):
    with pytest.raises(ValueError):
        story_factory(url="http://reuters.com/story")
    with pytest.raises(ValueError):
        story_factory(url="https://reuters.com/story\nunsafe")
