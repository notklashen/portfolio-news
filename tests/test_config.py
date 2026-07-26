from pathlib import Path

import pytest

from portfolio_news.config import Settings
from portfolio_news.errors import ConfigurationError


def base_env(credentials: Path) -> dict[str, str]:
    return {
        "OPENAI_API_KEY": "key",
        "TELEGRAM_BOT_TOKEN": "token",
        "TELEGRAM_CHAT_ID": "chat",
        "GOOGLE_SPREADSHEET_ID": "spreadsheet",
        "GOOGLE_CREDENTIALS_FILE": str(credentials),
        "TZ": "Europe/Paris",
    }


def test_loads_environment_and_extends_domains(tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    env = base_env(credentials)
    env["ADDITIONAL_ALLOWED_DOMAINS"] = "Example.com, https://issuer.example/"
    settings = Settings.from_env(environ=env)
    assert settings.openai_model == "gpt-5.6-sol"
    assert settings.database_path == Path.home() / ".local/state/portfolio-news/portfolio_news.db"
    assert settings.lock_file == Path.home() / ".local/state/portfolio-news/portfolio_news.lock"
    assert "example.com" in settings.allowed_domains
    assert "issuer.example" in settings.allowed_domains


def test_dry_run_does_not_require_telegram(tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    env = base_env(credentials)
    env.pop("TELEGRAM_BOT_TOKEN")
    env.pop("TELEGRAM_CHAT_ID")
    settings = Settings.from_env(environ=env, dry_run=True)
    assert settings.telegram_bot_token is None


def test_live_run_requires_telegram(tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    env = base_env(credentials)
    env.pop("TELEGRAM_BOT_TOKEN")
    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        Settings.from_env(environ=env)


def test_rejects_missing_credentials_file(tmp_path):
    env = base_env(tmp_path / "missing.json")
    with pytest.raises(ConfigurationError, match="does not exist"):
        Settings.from_env(environ=env)


def test_rejects_oversized_item_limit(tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    env = base_env(credentials)
    env["MAX_PORTFOLIO_ITEMS"] = "6"
    with pytest.raises(ConfigurationError, match="MAX_PORTFOLIO_ITEMS"):
        Settings.from_env(environ=env)


def test_rejects_more_than_one_hundred_allowed_domains(tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}", encoding="utf-8")
    env = base_env(credentials)
    env["ADDITIONAL_ALLOWED_DOMAINS"] = ",".join(
        f"publisher-{index}.example.com" for index in range(100)
    )
    with pytest.raises(ConfigurationError, match="100 domains"):
        Settings.from_env(environ=env)
