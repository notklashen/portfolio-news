#!/bin/sh
set -eu

ACTION="${1:-}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CRON_TEMPLATE="$SCRIPT_DIR/portfolio-news.cron"

if [ "$ACTION" != "install" ] && [ "$ACTION" != "remove" ]; then
    printf 'Usage: %s install|remove\n' "$0" >&2
    exit 2
fi

if ! command -v crontab >/dev/null 2>&1; then
    printf 'portfolio-news: crontab is not installed on this server\n' >&2
    exit 1
fi

CURRENT=$(mktemp)
UPDATED=$(mktemp)
trap 'rm -f "$CURRENT" "$UPDATED"' EXIT HUP INT TERM

if ! crontab -l >"$CURRENT" 2>/dev/null; then
    : >"$CURRENT"
fi

awk '
    $0 == "# BEGIN PORTFOLIO_NEWS" { managed = 1; next }
    $0 == "# END PORTFOLIO_NEWS" { managed = 0; next }
    !managed { print }
' "$CURRENT" >"$UPDATED"

if [ "$ACTION" = "install" ]; then
    if [ -s "$UPDATED" ]; then
        printf '\n' >>"$UPDATED"
    fi
    cat "$CRON_TEMPLATE" >>"$UPDATED"
fi

crontab "$UPDATED"
printf 'portfolio-news cron entry %sed for user %s\n' "$ACTION" "$(id -un)"
