"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from .config import Settings
from .errors import AlreadyRunningError, ConfigurationError, PortfolioNewsError
from .logging_config import configure_logging
from .orchestrator import build_orchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m portfolio_news",
        description="Research and deliver a portfolio-news Telegram digest.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run one digest cycle")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="research and render without preparing or sending a Telegram message",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env(dry_run=args.dry_run)
    except ConfigurationError as exc:
        configure_logging("ERROR")
        logging.getLogger(__name__).error("configuration_error", extra={"detail": str(exc)})
        return 2

    configure_logging(
        settings.log_level,
        secrets=(settings.openai_api_key, settings.telegram_bot_token or ""),
    )
    try:
        outcome = build_orchestrator(settings).run(dry_run=args.dry_run)
    except AlreadyRunningError as exc:
        logging.getLogger(__name__).warning("run_skipped_lock_held", extra={"detail": str(exc)})
        return 75
    except PortfolioNewsError as exc:
        logging.getLogger(__name__).error(
            "portfolio_news_error",
            extra={"error_type": type(exc).__name__, "detail": str(exc)},
        )
        return 1
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "unexpected_error", extra={"error_type": type(exc).__name__}
        )
        return 1
    if args.dry_run:
        print(outcome.rendered_html)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
