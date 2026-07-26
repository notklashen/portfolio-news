"""Non-blocking process lock for timer/manual-run overlap protection."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Optional, Type

from .errors import AlreadyRunningError


class SingleRunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def __enter__(self) -> "SingleRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._file.close()
            self._file = None
            raise AlreadyRunningError("Another portfolio-news run is already active") from exc
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()))
        self._file.flush()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
