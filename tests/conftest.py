from __future__ import annotations

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from portfolio_news.config import Settings
from portfolio_news.models import DigestStory, StoryCategory


@pytest.fixture
def settings_factory(tmp_path: Path):
    def factory(**overrides):
        values = {
            "openai_api_key": "sk-test-secret-value",
            "telegram_bot_token": "123456:telegram-secret-token-value",
            "telegram_chat_id": "42",
            "google_spreadsheet_id": "sheet-id",
            "google_credentials_file": tmp_path / "credentials.json",
            "timezone": ZoneInfo("Europe/Paris"),
            "timezone_name": "Europe/Paris",
            "database_path": tmp_path / "history.db",
            "lock_file": tmp_path / "run.lock",
            "openai_model": "gpt-5.6-sol",
            "allowed_domains": ("reuters.com", "sec.gov"),
            "max_portfolio_items": 5,
            "max_macro_items": 2,
            "max_history_items": 100,
            "history_days": 30,
            "max_telegram_chars": 3500,
            "openai_timeout_seconds": 180.0,
            "http_timeout_seconds": 30.0,
            "max_api_attempts": 3,
            "log_level": "INFO",
        }
        values.update(overrides)
        return Settings(**values)

    return factory


@pytest.fixture
def story_factory():
    def factory(**overrides):
        values = {
            "category": StoryCategory.PORTFOLIO,
            "affected_tickers": ["NASDAQ:GOOG"],
            "headline": "Alphabet publishes a material product update",
            "relevance_summary": "The confirmed change may affect revenue growth for the held company.",
            "publisher": "Reuters",
            "url": "https://www.reuters.com/technology/alphabet-update-2026-07-26/",
            "event_key": "alphabet-product-update-2026-07",
            "material_update": False,
            "publication_date": date(2026, 7, 26),
        }
        values.update(overrides)
        return DigestStory(**values)

    return factory
