import logging

import pytest

from portfolio_news.errors import AlreadyRunningError
from portfolio_news.lock import SingleRunLock
from portfolio_news.logging_config import configure_logging


def test_single_run_lock_rejects_overlap(tmp_path):
    path = tmp_path / "run.lock"
    with SingleRunLock(path):
        with pytest.raises(AlreadyRunningError):
            with SingleRunLock(path):
                pass
    with SingleRunLock(path):
        pass


def test_json_logging_redacts_known_secrets(capsys):
    api_key = "sk-super-secret-value"
    bot_token = "123456:telegram-secret-token-value"
    configure_logging("INFO", secrets=(api_key, bot_token))
    logging.getLogger("test").info("values %s %s", api_key, bot_token)
    output = capsys.readouterr().err
    assert api_key not in output
    assert bot_token not in output
    assert output.count("[REDACTED]") >= 2
