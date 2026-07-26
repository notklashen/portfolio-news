"""Telegram-safe HTML rendering and outbound Bot API delivery."""

from __future__ import annotations

from datetime import datetime
import html
import logging
from typing import Any, Optional

from .errors import TelegramError, TelegramRenderError
from .models import DigestStory, ResearchDigest, StoryCategory
from .retrying import is_transient_error, retry_call


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)].rstrip() + "…"


def _story_block(story: DigestStory) -> str:
    tickers = ", ".join(story.affected_tickers) if story.affected_tickers else "Portfolio-wide"
    ticker_label = html.escape(_truncate(tickers, 100))
    headline = html.escape(_truncate(story.headline, 150))
    relevance = html.escape(_truncate(story.relevance_summary, 240))
    publisher = html.escape(_truncate(story.publisher, 70))
    url = html.escape(story.url, quote=True)
    update = " <i>Material update</i>" if story.material_update else ""
    return (
        f"<b>{ticker_label}</b> — {headline}{update}\n"
        f"{relevance}\n"
        f'<a href="{url}">{publisher}</a>'
    )


def render_digest(digest: ResearchDigest, *, when: datetime, max_chars: int = 3500) -> str:
    date_label = f"{when.day} {_MONTHS[when.month - 1]} {when.year}"
    heading = f"<b>Portfolio news — {date_label}</b>"
    if not digest.stories:
        rendered = heading + "\n\nNo material new portfolio news today."
        if len(rendered) > max_chars:
            raise TelegramRenderError("MAX_TELEGRAM_CHARS is too small for the heartbeat")
        return rendered

    parts = [heading]
    omitted = 0
    sections = (
        (StoryCategory.PORTFOLIO, "Portfolio"),
        (StoryCategory.MACRO_GEOPOLITICAL, "Macro & geopolitical"),
    )
    for category, title in sections:
        category_stories = [story for story in digest.stories if story.category is category]
        section_started = False
        for story in category_stories:
            additions = []
            if not section_started:
                additions.append(f"<b>{title}</b>")
            additions.append(_story_block(story))
            candidate = "\n\n".join([*parts, *additions])
            if len(candidate) <= max_chars:
                parts.extend(additions)
                section_started = True
            else:
                omitted += 1
    if len(parts) == 1:
        raise TelegramRenderError("No digest story can fit within MAX_TELEGRAM_CHARS")
    rendered = "\n\n".join(parts)
    if omitted:
        note = f"\n\n<i>{omitted} additional material item{'s' if omitted != 1 else ''} omitted for length.</i>"
        if len(rendered) + len(note) <= max_chars:
            rendered += note
    if len(rendered) > max_chars or len(rendered) > 4096:
        raise TelegramRenderError("Rendered Telegram digest exceeds the configured limit")
    return rendered


class _TelegramHTTPError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _is_retryable_telegram_error(exc: BaseException) -> bool:
    if isinstance(exc, _TelegramHTTPError) and exc.status_code is None:
        return True
    return is_transient_error(exc)


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout_seconds: float = 30,
        attempts: int = 3,
        http_client: Optional[Any] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._http_client = http_client
        self.log = logger or logging.getLogger(__name__)

    @property
    def http_client(self) -> Any:
        if self._http_client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - deployment dependency guard
                raise TelegramError("The HTTP client dependency is not installed") from exc
            self._http_client = httpx.Client(timeout=self.timeout_seconds)
        return self._http_client

    def send(self, rendered_html: str) -> int:
        if len(rendered_html) > 4096:
            raise TelegramError("Telegram message exceeds the 4096-character API limit")

        def request() -> int:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            try:
                response = self.http_client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": rendered_html,
                        "parse_mode": "HTML",
                        "link_preview_options": {"is_disabled": True},
                    },
                )
            except Exception as exc:
                message = str(exc).replace(self.bot_token, "[REDACTED]")
                wrapped = _TelegramHTTPError(
                    f"Telegram transport error ({type(exc).__name__}): {message}"
                )
                wrapped.__cause__ = exc
                raise wrapped
            status_code = int(getattr(response, "status_code", 0) or 0)
            try:
                payload = response.json()
            except Exception as exc:
                raise _TelegramHTTPError("Telegram returned a non-JSON response", status_code) from exc
            if status_code >= 400 or not payload.get("ok"):
                error_code = payload.get("error_code") or status_code or None
                description = str(payload.get("description", "Telegram API rejected the message"))[:300]
                raise _TelegramHTTPError(description, int(error_code) if error_code else None)
            message_id = payload.get("result", {}).get("message_id")
            if not isinstance(message_id, int):
                raise _TelegramHTTPError("Telegram response omitted result.message_id")
            return message_id

        try:
            message_id = retry_call(
                request,
                operation="telegram_send_message",
                attempts=self.attempts,
                retryable=_is_retryable_telegram_error,
                logger=self.log,
            )
        except Exception as exc:
            safe_message = str(exc).replace(self.bot_token, "[REDACTED]")
            raise TelegramError(f"Telegram delivery failed: {safe_message}") from exc
        self.log.info("telegram_delivered", extra={"telegram_message_id": message_id})
        return message_id
