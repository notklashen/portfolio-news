"""Structured JSON logging with defensive secret redaction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import sys
from typing import Iterable


_STANDARD_FIELDS = set(logging.makeLogRecord({}).__dict__)
_TOKEN_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b"),
)


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        rendered = json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))
        for secret in self._secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        for pattern in _TOKEN_PATTERNS:
            rendered = pattern.sub("[REDACTED]", rendered)
        return rendered


def configure_logging(level: str, *, secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

