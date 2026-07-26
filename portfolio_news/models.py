"""Validated data exchanged between research, history, and delivery layers."""

from __future__ import annotations

from datetime import date
from enum import Enum
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StoryCategory(str, Enum):
    PORTFOLIO = "portfolio"
    MACRO_GEOPOLITICAL = "macro_geopolitical"


class DigestStory(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: StoryCategory
    affected_tickers: list[str] = Field(default_factory=list, max_length=50)
    headline: str = Field(min_length=3, max_length=240)
    relevance_summary: str = Field(min_length=3, max_length=700)
    publisher: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=8, max_length=2048)
    event_key: str = Field(min_length=3, max_length=180)
    material_update: bool = False
    publication_date: date

    @field_validator("affected_tickers")
    @classmethod
    def normalize_tickers(cls, tickers: list[str]) -> list[str]:
        normalized: list[str] = []
        for ticker in tickers:
            value = ticker.strip().upper()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    @field_validator("url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("url cannot contain control characters")
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("url must be an absolute HTTPS URL without credentials")
        return value

    @field_validator("event_key")
    @classmethod
    def normalize_event_key(cls, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        if len(normalized) < 3:
            raise ValueError("event_key must contain at least three alphanumeric characters")
        return normalized[:180]


class ResearchDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stories: list[DigestStory] = Field(default_factory=list, max_length=7)
