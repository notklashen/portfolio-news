"""Environment-backed application configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os

from .errors import ConfigurationError
from .sources import DEFAULT_ALLOWED_DOMAINS, normalize_domain


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _number(env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum:g} and {maximum:g}")
    return value


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]
    google_spreadsheet_id: str
    google_credentials_file: Path
    timezone: ZoneInfo
    timezone_name: str
    database_path: Path
    lock_file: Path
    openai_model: str
    allowed_domains: tuple[str, ...]
    max_portfolio_items: int
    max_macro_items: int
    max_history_items: int
    history_days: int
    max_telegram_chars: int
    openai_timeout_seconds: float
    http_timeout_seconds: float
    max_api_attempts: int
    log_level: str

    @classmethod
    def from_env(
        cls,
        *,
        dry_run: bool = False,
        environ: Optional[Mapping[str, str]] = None,
        validate_files: bool = True,
    ) -> "Settings":
        env = os.environ if environ is None else environ
        timezone_name = env.get("TZ", "Europe/Paris").strip() or "Europe/Paris"
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"Unknown TZ value: {timezone_name}") from exc

        credentials_file = Path(_required(env, "GOOGLE_CREDENTIALS_FILE")).expanduser()
        if validate_files and not credentials_file.is_file():
            raise ConfigurationError(
                f"GOOGLE_CREDENTIALS_FILE does not exist or is not a file: {credentials_file}"
            )

        additional: list[str] = []
        for raw_domain in env.get("ADDITIONAL_ALLOWED_DOMAINS", "").split(","):
            if raw_domain.strip():
                try:
                    additional.append(normalize_domain(raw_domain))
                except ValueError as exc:
                    raise ConfigurationError(
                        f"Invalid domain in ADDITIONAL_ALLOWED_DOMAINS: {raw_domain.strip()}"
                    ) from exc
        allowed_domains = tuple(dict.fromkeys((*DEFAULT_ALLOWED_DOMAINS, *additional)))
        if len(allowed_domains) > 100:
            raise ConfigurationError(
                "The combined source allowlist cannot exceed the web_search limit of 100 domains"
            )

        token = env.get("TELEGRAM_BOT_TOKEN", "").strip() or None
        chat_id = env.get("TELEGRAM_CHAT_ID", "").strip() or None
        if not dry_run:
            if token is None:
                raise ConfigurationError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
            if chat_id is None:
                raise ConfigurationError("Missing required environment variable: TELEGRAM_CHAT_ID")

        log_level = env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

        return cls(
            openai_api_key=_required(env, "OPENAI_API_KEY"),
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            google_spreadsheet_id=_required(env, "GOOGLE_SPREADSHEET_ID"),
            google_credentials_file=credentials_file,
            timezone=timezone,
            timezone_name=timezone_name,
            database_path=Path(
                env.get("DATABASE_PATH", "/var/lib/portfolio-news/portfolio_news.db")
            ).expanduser(),
            lock_file=Path(
                env.get("LOCK_FILE", "/var/lib/portfolio-news/portfolio_news.lock")
            ).expanduser(),
            openai_model=env.get("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol",
            allowed_domains=allowed_domains,
            max_portfolio_items=_integer(env, "MAX_PORTFOLIO_ITEMS", 5, 1, 5),
            max_macro_items=_integer(env, "MAX_MACRO_ITEMS", 2, 0, 2),
            max_history_items=_integer(env, "MAX_HISTORY_ITEMS", 100, 1, 500),
            history_days=_integer(env, "HISTORY_DAYS", 30, 1, 90),
            max_telegram_chars=_integer(env, "MAX_TELEGRAM_CHARS", 3500, 500, 4096),
            openai_timeout_seconds=_number(env, "OPENAI_TIMEOUT_SECONDS", 180, 10, 600),
            http_timeout_seconds=_number(env, "HTTP_TIMEOUT_SECONDS", 30, 5, 120),
            max_api_attempts=_integer(env, "MAX_API_ATTEMPTS", 3, 1, 6),
            log_level=log_level,
        )
