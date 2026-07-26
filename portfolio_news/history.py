"""SQLite persistence for runs, prepared deliveries, and deduplication history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional

from .models import ResearchDigest
from .sources import canonicalize_url


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class PreparedDigest:
    id: int
    created_at: datetime
    lookback_start: datetime
    lookback_end: datetime
    digest_date: str
    rendered_html: str
    digest: ResearchDigest
    openai_response_id: Optional[str]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    web_search_calls: int
    source_metadata: list[dict[str, Any]]


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = FULL;

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    lookback_start TEXT,
                    lookback_end TEXT,
                    prepared_digest_id INTEGER,
                    error TEXT,
                    openai_response_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    web_search_calls INTEGER NOT NULL DEFAULT 0,
                    source_metadata_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (prepared_digest_id) REFERENCES prepared_digests(id)
                );

                CREATE TABLE IF NOT EXISTS prepared_digests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    lookback_start TEXT NOT NULL,
                    lookback_end TEXT NOT NULL,
                    digest_date TEXT NOT NULL,
                    rendered_html TEXT NOT NULL,
                    stories_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('prepared', 'delivered')),
                    openai_response_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    web_search_calls INTEGER NOT NULL DEFAULT 0,
                    source_metadata_json TEXT NOT NULL DEFAULT '[]',
                    telegram_message_id INTEGER,
                    delivered_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_prepared_digest
                    ON prepared_digests(status) WHERE status = 'prepared';

                CREATE TABLE IF NOT EXISTS delivered_stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prepared_digest_id INTEGER NOT NULL,
                    delivered_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    tickers_json TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    relevance_summary TEXT NOT NULL,
                    publisher TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL UNIQUE,
                    publication_date TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    material_update INTEGER NOT NULL DEFAULT 0,
                    telegram_message_id INTEGER NOT NULL,
                    openai_response_id TEXT,
                    FOREIGN KEY (prepared_digest_id) REFERENCES prepared_digests(id)
                );

                CREATE INDEX IF NOT EXISTS delivered_stories_event_key
                    ON delivered_stories(event_key, delivered_at DESC);
                CREATE INDEX IF NOT EXISTS delivered_stories_delivered_at
                    ON delivered_stories(delivered_at DESC);

                CREATE TABLE IF NOT EXISTS delivered_recaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prepared_digest_id INTEGER NOT NULL,
                    delivered_at TEXT NOT NULL,
                    section TEXT NOT NULL,
                    category TEXT NOT NULL,
                    tickers_json TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    relevance_summary TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    material_update INTEGER NOT NULL DEFAULT 0,
                    telegram_message_id INTEGER NOT NULL,
                    openai_response_id TEXT,
                    FOREIGN KEY (prepared_digest_id) REFERENCES prepared_digests(id)
                );

                CREATE INDEX IF NOT EXISTS delivered_recaps_delivered_at
                    ON delivered_recaps(delivered_at DESC);
                """
            )

    def start_run(self, *, dry_run: bool, started_at: Optional[datetime] = None) -> int:
        started = started_at or utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = 'interrupted', finished_at = ?,
                    error = 'Process ended before recording a terminal run state'
                WHERE status = 'running'
                """,
                (to_utc_iso(started),),
            )
            cursor = connection.execute(
                "INSERT INTO runs(started_at, status, dry_run) VALUES (?, 'running', ?)",
                (to_utc_iso(started), int(dry_run)),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        finished_at: Optional[datetime] = None,
        lookback_start: Optional[datetime] = None,
        lookback_end: Optional[datetime] = None,
        prepared_digest_id: Optional[int] = None,
        error: Optional[str] = None,
        openai_response_id: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        web_search_calls: int = 0,
        source_metadata: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE runs
                SET finished_at = ?, status = ?, lookback_start = ?, lookback_end = ?,
                    prepared_digest_id = ?, error = ?, openai_response_id = ?,
                    input_tokens = ?, output_tokens = ?, total_tokens = ?, web_search_calls = ?,
                    source_metadata_json = ?
                WHERE id = ?
                """,
                (
                    to_utc_iso(finished_at or utc_now()),
                    status,
                    to_utc_iso(lookback_start) if lookback_start else None,
                    to_utc_iso(lookback_end) if lookback_end else None,
                    prepared_digest_id,
                    error[:1000] if error else None,
                    openai_response_id,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    web_search_calls,
                    json.dumps(source_metadata or [], ensure_ascii=False, separators=(",", ":")),
                    run_id,
                ),
            )

    def create_prepared_digest(
        self,
        *,
        digest_date: str,
        lookback_start: datetime,
        lookback_end: datetime,
        rendered_html: str,
        digest: ResearchDigest,
        openai_response_id: Optional[str],
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        web_search_calls: int,
        source_metadata: list[dict[str, Any]],
        created_at: Optional[datetime] = None,
    ) -> int:
        stories_json = digest.model_dump_json()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO prepared_digests(
                    created_at, lookback_start, lookback_end, digest_date, rendered_html,
                    stories_json, status, openai_response_id, input_tokens, output_tokens,
                    total_tokens, web_search_calls, source_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?, ?, ?)
                """,
                (
                    to_utc_iso(created_at or utc_now()),
                    to_utc_iso(lookback_start),
                    to_utc_iso(lookback_end),
                    digest_date,
                    rendered_html,
                    stories_json,
                    openai_response_id,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    web_search_calls,
                    json.dumps(source_metadata, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)

    def pending_prepared_digest(self) -> Optional[PreparedDigest]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM prepared_digests WHERE status = 'prepared' ORDER BY id LIMIT 1"
            ).fetchone()
        return self._prepared_from_row(row) if row else None

    def get_prepared_digest(self, prepared_id: int) -> Optional[PreparedDigest]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM prepared_digests WHERE id = ?", (prepared_id,)
            ).fetchone()
        return self._prepared_from_row(row) if row else None

    @staticmethod
    def _prepared_from_row(row: sqlite3.Row) -> PreparedDigest:
        return PreparedDigest(
            id=int(row["id"]),
            created_at=parse_datetime(row["created_at"]),
            lookback_start=parse_datetime(row["lookback_start"]),
            lookback_end=parse_datetime(row["lookback_end"]),
            digest_date=row["digest_date"],
            rendered_html=row["rendered_html"],
            digest=ResearchDigest.model_validate_json(row["stories_json"]),
            openai_response_id=row["openai_response_id"],
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            total_tokens=int(row["total_tokens"]),
            web_search_calls=int(row["web_search_calls"]),
            source_metadata=json.loads(row["source_metadata_json"]),
        )

    def mark_prepared_delivered(
        self,
        prepared_id: int,
        *,
        telegram_message_id: int,
        delivered_at: Optional[datetime] = None,
    ) -> None:
        delivered = delivered_at or utc_now()
        delivered_iso = to_utc_iso(delivered)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM prepared_digests WHERE id = ?", (prepared_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Prepared digest {prepared_id} does not exist")
            if row["status"] == "delivered":
                return
            digest = ResearchDigest.model_validate_json(row["stories_json"])
            for story in digest.stories:
                connection.execute(
                    """
                    INSERT INTO delivered_recaps(
                        prepared_digest_id, delivered_at, section, category, tickers_json,
                        headline, relevance_summary, citations_json, event_key,
                        material_update, telegram_message_id, openai_response_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prepared_id,
                        delivered_iso,
                        story.section,
                        story.category.value,
                        json.dumps(story.affected_tickers, separators=(",", ":")),
                        story.headline,
                        story.relevance_summary,
                        json.dumps(
                            [citation.model_dump() for citation in story.all_citations],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        story.event_key,
                        int(story.material_update),
                        telegram_message_id,
                        row["openai_response_id"],
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO delivered_stories(
                        prepared_digest_id, delivered_at, category, tickers_json, headline,
                        relevance_summary, publisher, url, canonical_url, publication_date,
                        event_key, material_update, telegram_message_id, openai_response_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prepared_id,
                        delivered_iso,
                        story.category.value,
                        json.dumps(story.affected_tickers, separators=(",", ":")),
                        story.headline,
                        story.relevance_summary,
                        story.publisher,
                        story.url,
                        canonicalize_url(story.url),
                        story.publication_date.isoformat(),
                        story.event_key,
                        int(story.material_update),
                        telegram_message_id,
                        row["openai_response_id"],
                    ),
                )
            connection.execute(
                """
                UPDATE prepared_digests
                SET status = 'delivered', telegram_message_id = ?, delivered_at = ?
                WHERE id = ?
                """,
                (telegram_message_id, delivered_iso, prepared_id),
            )

    def last_successful_coverage_end(self) -> Optional[datetime]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT lookback_end
                FROM prepared_digests
                WHERE status = 'delivered'
                ORDER BY delivered_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return parse_datetime(row["lookback_end"]) if row else None

    def recent_delivered(self, *, days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        cutoff = to_utc_iso(utc_now() - timedelta(days=days))
        with self._connection() as connection:
            recap_rows = connection.execute(
                """
                SELECT delivered_at, section, category, tickers_json, headline,
                       relevance_summary, citations_json, event_key, material_update
                FROM delivered_recaps
                WHERE delivered_at >= ?
                ORDER BY delivered_at DESC, id DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
            remaining = max(0, limit - len(recap_rows))
            legacy_rows = connection.execute(
                """
                SELECT ds.delivered_at, ds.category, ds.tickers_json, ds.headline,
                       ds.relevance_summary, ds.publisher, ds.canonical_url,
                       ds.publication_date, ds.event_key, ds.material_update
                FROM delivered_stories AS ds
                WHERE ds.delivered_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM delivered_recaps AS dr
                      WHERE dr.prepared_digest_id = ds.prepared_digest_id
                  )
                ORDER BY ds.delivered_at DESC, ds.id DESC
                LIMIT ?
                """,
                (cutoff, remaining),
            ).fetchall()
        recaps = [
            {
                "delivered_at": row["delivered_at"],
                "section": row["section"],
                "category": row["category"],
                "affected_tickers": json.loads(row["tickers_json"]),
                "headline": row["headline"],
                "relevance_summary": row["relevance_summary"],
                "citations": json.loads(row["citations_json"]),
                "event_key": row["event_key"],
                "material_update": bool(row["material_update"]),
            }
            for row in recap_rows
        ]
        legacy = [
            {
                "delivered_at": row["delivered_at"],
                "category": row["category"],
                "affected_tickers": json.loads(row["tickers_json"]),
                "headline": row["headline"],
                "relevance_summary": row["relevance_summary"],
                "publisher": row["publisher"],
                "canonical_url": row["canonical_url"],
                "publication_date": row["publication_date"],
                "event_key": row["event_key"],
                "material_update": bool(row["material_update"]),
            }
            for row in legacy_rows
        ]
        return [*recaps, *legacy][:limit]

    def url_was_delivered(self, canonical_url: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM delivered_stories WHERE canonical_url = ? LIMIT 1",
                (canonical_url,),
            ).fetchone()
        return row is not None

    def latest_event(self, event_key: str) -> Optional[dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT delivered_at, headline, relevance_summary, canonical_url, material_update
                FROM delivered_stories
                WHERE event_key = ?
                ORDER BY delivered_at DESC, id DESC
                LIMIT 1
                """,
                (event_key,),
            ).fetchone()
        return dict(row) if row else None

    def delivered_story_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM delivered_recaps) +
                    (SELECT COUNT(*) FROM delivered_stories AS ds
                     WHERE NOT EXISTS (
                         SELECT 1 FROM delivered_recaps AS dr
                         WHERE dr.prepared_digest_id = ds.prepared_digest_id
                     )) AS count
                """
            ).fetchone()
        return int(row["count"])
