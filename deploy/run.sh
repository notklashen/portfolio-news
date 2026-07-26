#!/bin/sh
set -eu

APP_DIR="${PORTFOLIO_NEWS_APP_DIR:-$HOME/portfolio-news}"
ENV_FILE="${PORTFOLIO_NEWS_ENV_FILE:-$HOME/.config/portfolio-news/portfolio-news.env}"
PYTHON="$APP_DIR/.venv/bin/python"

if [ ! -r "$ENV_FILE" ]; then
    printf 'portfolio-news: environment file is not readable: %s\n' "$ENV_FILE" >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    printf 'portfolio-news: virtual-environment Python is not executable: %s\n' "$PYTHON" >&2
    exit 1
fi

set -a
# The file is private user-owned configuration and uses shell-compatible KEY=value lines.
. "$ENV_FILE"
set +a

cd "$APP_DIR"
exec "$PYTHON" -m portfolio_news run "$@"
