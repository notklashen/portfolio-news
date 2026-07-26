"""Single-run workflow coordinating research, persistence, and delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Callable, Optional

from .config import Settings
from .history import HistoryStore, PreparedDigest
from .lock import SingleRunLock
from .models import ResearchDigest
from .research import OpenAIResearcher, ResearchResult
from .sheets import SheetsPortfolioReader
from .sources import canonicalize_url
from .telegram import TelegramClient, render_digest


MAX_LOOKBACK_DAYS = 4


def calculate_lookback_start(
    now: datetime,
    last_successful_coverage_end: Optional[datetime],
    *,
    max_days: int = MAX_LOOKBACK_DAYS,
) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    floor = now - timedelta(days=max_days)
    if last_successful_coverage_end is None:
        return floor
    if last_successful_coverage_end.tzinfo is None:
        raise ValueError("last_successful_coverage_end must be timezone-aware")
    if last_successful_coverage_end > now:
        return now
    return max(floor, last_successful_coverage_end)


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    status: str
    rendered_html: str
    story_count: int
    telegram_message_id: Optional[int] = None
    reused_prepared_digest: bool = False


class PortfolioNewsOrchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        history: Optional[HistoryStore] = None,
        sheets: Optional[SheetsPortfolioReader] = None,
        researcher: Optional[OpenAIResearcher] = None,
        telegram: Optional[TelegramClient] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        lock_factory: Callable[..., SingleRunLock] = SingleRunLock,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.settings = settings
        self.history = history or HistoryStore(settings.database_path)
        self.sheets = sheets or SheetsPortfolioReader(
            settings.google_spreadsheet_id,
            settings.google_credentials_file,
            timeout_seconds=settings.http_timeout_seconds,
            attempts=settings.max_api_attempts,
        )
        self.researcher = researcher or OpenAIResearcher(
            settings.openai_api_key,
            model=settings.openai_model,
            allowed_domains=settings.allowed_domains,
            max_portfolio_items=settings.max_portfolio_items,
            max_macro_items=settings.max_macro_items,
            timeout_seconds=settings.openai_timeout_seconds,
            attempts=settings.max_api_attempts,
        )
        if telegram is not None:
            self.telegram = telegram
        elif settings.telegram_bot_token and settings.telegram_chat_id:
            self.telegram = TelegramClient(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                timeout_seconds=settings.http_timeout_seconds,
                attempts=settings.max_api_attempts,
            )
        else:
            self.telegram = None
        self.now_provider = now_provider or (lambda: datetime.now(settings.timezone))
        self.lock_factory = lock_factory
        self.log = logger or logging.getLogger(__name__)

    def run(self, *, dry_run: bool = False) -> RunOutcome:
        with self.lock_factory(self.settings.lock_file):
            self.history.initialize()
            now = self.now_provider()
            if now.tzinfo is None:
                raise ValueError("now_provider must return a timezone-aware datetime")
            run_id = self.history.start_run(dry_run=dry_run, started_at=now)
            lookback_start: Optional[datetime] = None
            result: Optional[ResearchResult] = None
            prepared_id: Optional[int] = None
            finished = False
            try:
                if not dry_run:
                    pending = self.history.pending_prepared_digest()
                    if pending is not None:
                        prepared_id = pending.id
                        lookback_start = pending.lookback_start
                        outcome = self._deliver_pending(run_id, pending, now)
                        finished = True
                        return outcome

                last_coverage = self.history.last_successful_coverage_end()
                lookback_start = calculate_lookback_start(now, last_coverage)
                tickers = self.sheets.read_tickers()
                recent_history = self.history.recent_delivered(
                    days=self.settings.history_days,
                    limit=self.settings.max_history_items,
                )
                result = self.researcher.research(
                    tickers,
                    lookback_start=lookback_start,
                    lookback_end=now,
                    recent_history=recent_history,
                )
                digest = self._deduplicate(result.digest)
                rendered = render_digest(
                    digest,
                    when=now,
                    max_chars=self.settings.max_telegram_chars,
                )

                if dry_run:
                    self.history.finish_run(
                        run_id,
                        status="dry_run",
                        finished_at=now,
                        lookback_start=lookback_start,
                        lookback_end=now,
                        openai_response_id=result.response_id,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        total_tokens=result.total_tokens,
                        web_search_calls=result.web_search_calls,
                        source_metadata=result.source_metadata,
                    )
                    finished = True
                    self.log.info(
                        "dry_run_complete",
                        extra={"run_id": run_id, "story_count": len(digest.stories)},
                    )
                    return RunOutcome(
                        run_id=run_id,
                        status="dry_run",
                        rendered_html=rendered,
                        story_count=len(digest.stories),
                    )

                prepared_id = self.history.create_prepared_digest(
                    digest_date=now.date().isoformat(),
                    lookback_start=lookback_start,
                    lookback_end=now,
                    rendered_html=rendered,
                    digest=digest,
                    openai_response_id=result.response_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    total_tokens=result.total_tokens,
                    web_search_calls=result.web_search_calls,
                    source_metadata=result.source_metadata,
                    created_at=now,
                )
                if self.telegram is None:
                    raise RuntimeError("Telegram client is unavailable for a live run")
                message_id = self.telegram.send(rendered)
                self.history.mark_prepared_delivered(
                    prepared_id,
                    telegram_message_id=message_id,
                    delivered_at=now,
                )
                self.history.finish_run(
                    run_id,
                    status="success",
                    finished_at=now,
                    lookback_start=lookback_start,
                    lookback_end=now,
                    prepared_digest_id=prepared_id,
                    openai_response_id=result.response_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    total_tokens=result.total_tokens,
                    web_search_calls=result.web_search_calls,
                    source_metadata=result.source_metadata,
                )
                finished = True
                self.log.info(
                    "digest_run_complete",
                    extra={
                        "run_id": run_id,
                        "story_count": len(digest.stories),
                        "telegram_message_id": message_id,
                    },
                )
                return RunOutcome(
                    run_id=run_id,
                    status="success",
                    rendered_html=rendered,
                    story_count=len(digest.stories),
                    telegram_message_id=message_id,
                )
            except Exception as exc:
                if not finished:
                    self.history.finish_run(
                        run_id,
                        status="failed",
                        finished_at=self.now_provider(),
                        lookback_start=lookback_start,
                        lookback_end=now,
                        prepared_digest_id=prepared_id,
                        error=f"{type(exc).__name__}: {exc}",
                        openai_response_id=result.response_id if result else None,
                        input_tokens=result.input_tokens if result else 0,
                        output_tokens=result.output_tokens if result else 0,
                        total_tokens=result.total_tokens if result else 0,
                        web_search_calls=result.web_search_calls if result else 0,
                        source_metadata=result.source_metadata if result else [],
                    )
                self.log.error(
                    "digest_run_failed",
                    extra={"run_id": run_id, "error_type": type(exc).__name__},
                )
                raise

    def _deliver_pending(
        self, run_id: int, pending: PreparedDigest, delivered_at: datetime
    ) -> RunOutcome:
        if self.telegram is None:
            raise RuntimeError("Telegram client is unavailable for a live run")
        self.log.info(
            "reusing_prepared_digest",
            extra={"run_id": run_id, "prepared_digest_id": pending.id},
        )
        message_id = self.telegram.send(pending.rendered_html)
        self.history.mark_prepared_delivered(
            pending.id,
            telegram_message_id=message_id,
            delivered_at=delivered_at,
        )
        self.history.finish_run(
            run_id,
            status="success",
            finished_at=delivered_at,
            lookback_start=pending.lookback_start,
            lookback_end=pending.lookback_end,
            prepared_digest_id=pending.id,
            openai_response_id=pending.openai_response_id,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            web_search_calls=0,
            source_metadata=[],
        )
        return RunOutcome(
            run_id=run_id,
            status="success",
            rendered_html=pending.rendered_html,
            story_count=len(pending.digest.stories),
            telegram_message_id=message_id,
            reused_prepared_digest=True,
        )

    def _deduplicate(self, digest: ResearchDigest) -> ResearchDigest:
        accepted = []
        seen_urls: set[str] = set()
        seen_events: set[str] = set()
        for story in digest.stories:
            canonical = canonicalize_url(story.url)
            reason: Optional[str] = None
            if canonical in seen_urls or story.event_key in seen_events:
                reason = "duplicate_in_digest"
            elif self.history.url_was_delivered(canonical):
                reason = "url_already_delivered"
            else:
                prior = self.history.latest_event(story.event_key)
                if prior is not None:
                    if not story.material_update:
                        reason = "event_already_delivered"
                    elif (
                        " ".join(story.relevance_summary.casefold().split())
                        == " ".join(str(prior["relevance_summary"]).casefold().split())
                    ):
                        reason = "material_update_has_no_new_summary"
            if reason:
                self.log.info(
                    "deduplicated_story",
                    extra={"reason": reason, "event_key": story.event_key},
                )
                continue
            accepted.append(story)
            seen_urls.add(canonical)
            seen_events.add(story.event_key)
        return ResearchDigest(stories=accepted)


def build_orchestrator(settings: Settings) -> PortfolioNewsOrchestrator:
    return PortfolioNewsOrchestrator(settings)
