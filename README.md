# Portfolio News

An outbound-only Telegram bot that reads a Google Sheets portfolio, researches daily price performance and market context with OpenAI web search, and sends one concise weekday recap. It does not trade, calculate portfolio values, handle Telegram commands, or expose a web service.

The production schedule is 08:00 Monday–Friday in `Europe/Paris`. Delivery history is retained indefinitely in SQLite; only the newest 30 days or 100 recap paragraphs are included in a research prompt.

## How one run works

1. A non-blocking file lock prevents timer and manual runs from overlapping.
2. Any previously prepared but undelivered digest is retried first, without calling Sheets or OpenAI again.
3. The workbook title is verified as exactly `PORTFOLIO_TRACKER`. Only the `overview` worksheet is read (matched case-insensitively), and it must contain exactly one case-insensitive `Ticker` cell.
4. Valid ticker cells beneath that header are trimmed, uppercased, and deduplicated. Other worksheets are ignored; malformed rows in `overview` are logged and skipped.
5. One OpenAI Responses API request researches every holding's latest web-verifiable movement plus relevant context. The catalyst lookback is capped at four days; clearly dated historical and upcoming context may fall outside it.
6. Search is unrestricted so quote sources can cover all asset classes. Code requires every internally cited URL to appear in the API's complete consulted-source metadata and requires every holding to be represented.
7. The grouped narrative HTML recap is prepared in SQLite before Telegram is called. It becomes delivered only after Telegram returns a message ID.

An empty result still sends `No verified portfolio market recap is available today.` as a health heartbeat.

## Google Sheets setup

1. In a Google Cloud project, enable the Google Sheets API.
2. Create a service account and download its JSON credentials. No Drive scope is needed.
3. Rename the target spreadsheet to exactly `PORTFOLIO_TRACKER`.
4. Share that spreadsheet as **Viewer** with the service account's `client_email` from the JSON file.
5. Copy the spreadsheet ID from the URL segment between `/d/` and `/edit` into `GOOGLE_SPREADSHEET_ID`.
6. Name the holdings worksheet `overview` (case-insensitively) and ensure it contains exactly one cell whose trimmed text is `Ticker`, also case-insensitively. Put holdings below that cell in the same column, for example `NASDAQ:GOOG`. `Ticker` columns in worksheets such as `transactions` are ignored.

The application uses only `https://www.googleapis.com/auth/spreadsheets.readonly`.

## Telegram setup

Create a bot with BotFather, open a private chat with it, and obtain that chat's numeric ID. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The runtime only calls `sendMessage`; it does not poll updates or register a webhook.

## Configuration

Copy `.env.example` to `.env`. Required live-run variables are:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GOOGLE_SPREADSHEET_ID`
- `GOOGLE_CREDENTIALS_FILE`
- `TZ` (defaults to `Europe/Paris`)

Useful optional controls are:

- `OPENAI_MODEL` (default `gpt-5.6-sol`)
- `MAX_HISTORY_ITEMS` and `HISTORY_DAYS`
- `MAX_TELEGRAM_CHARS` (default 3,500, hard maximum 4,096)
- API timeouts, retry count, database/lock paths, and `LOG_LEVEL`

For an existing deployment, update any `OPENAI_MODEL` entry in `~/.config/portfolio-news/portfolio-news.env` to `gpt-5.6-sol`; an explicit old value overrides the new application default.

Web search has no domain filter. The research prompt prefers official exchanges, issuer pages, established quote services, and reputable financial publishers; social posts, scraped snippets, rumors, and low-quality aggregators are prohibited. Sources are validated and retained internally but are not rendered in the Telegram recap.

## Local development

Python 3.9 through 3.14 is supported. Python 3.9 is end-of-life, so upgrade the server runtime when practical; the dependency pins deliberately retain compatibility with it.

```bash
python3.9 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
set -a
. ./.env
set +a
export GOOGLE_CREDENTIALS_FILE="$PWD/secrets/google_credentials.json"
export DATABASE_PATH="$PWD/data/portfolio_news.db"
export LOCK_FILE="$PWD/data/portfolio_news.lock"
pytest
```

Run without Telegram delivery or prepared-digest persistence:

```bash
python -m portfolio_news run --dry-run
```

Run live once:

```bash
python -m portfolio_news run
```

A dry run still reads Sheets and makes an OpenAI web-search request. It writes only a run audit row to SQLite and prints Telegram HTML to stdout; Telegram credentials are optional for that command.

## Lightsail installation without root access

The deployment is entirely owned by the Lightsail login account. It uses these locations:

- application and virtual environment: `~/portfolio-news`
- private configuration and Google credentials: `~/.config/portfolio-news`
- SQLite, the run lock, cron log, and backups: `~/.local/state/portfolio-news`

The following assumes Ubuntu, this repository is at `~/portfolio-news`, and `python3.9` includes the `venv` module. No command requires `sudo`. If `python3.9 -m venv` is unavailable, use another user-managed Python 3.9 installation or ask the server administrator to provide it.

```bash
python3.9 --version
python3.9 -m venv --help
cd "$HOME/portfolio-news"
umask 077
install -d -m 0700 "$HOME/.config/portfolio-news"
install -d -m 0700 "$HOME/.local/state/portfolio-news/backups"
install -m 0600 google-service-account.json "$HOME/.config/portfolio-news/google_credentials.json"
install -m 0600 .env.example "$HOME/.config/portfolio-news/portfolio-news.env"
vi "$HOME/.config/portfolio-news/portfolio-news.env"
python3.9 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps .
```

Replace every `replace-me` value in the private environment file. Its default paths already point to the user-owned configuration and state directories. Keep both secret files at mode `0600`.

Test the exact command used by the cron fallback, first without delivery and then live:

```bash
cd "$HOME/portfolio-news"
deploy/run.sh --dry-run
deploy/run.sh
```

### Scheduling

Choose exactly one scheduler so the digest is not launched twice.

User-level systemd is suitable only when the account's user manager remains active after logout. Check this without elevated privileges:

```bash
loginctl show-user "$USER" -p Linger
```

If it reports `Linger=yes`, install and activate the user timer:

```bash
install -d -m 0700 "$HOME/.config/systemd/user"
install -m 0644 deploy/portfolio-news.service "$HOME/.config/systemd/user/portfolio-news.service"
install -m 0644 deploy/portfolio-news.timer "$HOME/.config/systemd/user/portfolio-news.timer"
systemd-analyze --user verify "$HOME/.config/systemd/user/portfolio-news.service" "$HOME/.config/systemd/user/portfolio-news.timer"
systemctl --user daemon-reload
systemd-analyze calendar 'Mon..Fri *-*-* 08:00:00 Europe/Paris'
systemctl --user enable --now portfolio-news.timer
systemctl --user list-timers portfolio-news.timer --all
```

Confirm that `NEXT` is the next weekday at 08:00 Paris time. `Persistent=true` makes a missed firing run when the persistent user manager resumes. Enabling lingering is an administrator-controlled server setting; the deployment does not attempt to change it.

The unit retains process-level hardening that works in an unprivileged user manager. Secret and state isolation comes from the account-owned `0600` files and `0700` directories; it does not rely on privileged filesystem namespaces.

If `Linger=no`, systemd lacks timezone-qualified calendar support, or no user manager is available, use the account's existing crontab instead. The helper preserves unrelated entries and can be run repeatedly:

```bash
systemctl --user disable --now portfolio-news.timer 2>/dev/null || true
deploy/manage-cron.sh install
crontab -l
```

The cron entry checks Paris wall time explicitly each minute and launches once at 08:00 Monday–Friday, including across daylight-saving changes. It does not depend on the server timezone or `CRON_TZ`. The server's cron daemon must already be running; starting or installing that daemon requires the administrator.

## Operations

Manual run and log inspection:

```bash
cd "$HOME/portfolio-news"
deploy/run.sh
systemctl --user start portfolio-news.service
journalctl --user -u portfolio-news.service -n 200 --no-pager
journalctl --user -u portfolio-news.service -f
tail -f "$HOME/.local/state/portfolio-news/cron.log"
```

Use only the commands for the active scheduler. The application emits JSON logs. Known API keys and bot tokens are redacted; do not place secrets inside publisher-domain or path settings.

Back up SQLite with its online backup API:

```bash
"$HOME/portfolio-news/.venv/bin/python" -c 'import datetime, sqlite3; from pathlib import Path; state = Path.home() / ".local/state/portfolio-news"; name = state / "backups" / ("portfolio-news-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".db"); source = sqlite3.connect(str(state / "portfolio_news.db")); target = sqlite3.connect(str(name)); source.backup(target); target.close(); source.close(); print(name)'
```

SQLite's online backup API produces a consistent database even when WAL mode is enabled. Before restoring, disable the active scheduler with `systemctl --user disable --now portfolio-news.timer` or `deploy/manage-cron.sh remove`, make sure no manual run is active, and retain the current database until the restored copy is verified.

Review cumulative OpenAI usage recorded per run:

```bash
"$HOME/portfolio-news/.venv/bin/python" -c 'import sqlite3; from pathlib import Path; db = sqlite3.connect(str(Path.home() / ".local/state/portfolio-news/portfolio_news.db")); print(db.execute("select count(*),sum(input_tokens),sum(output_tokens),sum(web_search_calls) from (select input_tokens,output_tokens,web_search_calls from prepared_digests union all select input_tokens,output_tokens,web_search_calls from runs where dry_run=1 and status=\"dry_run\")").fetchone())'
```

Upgrade with a recorded rollback point:

```bash
systemctl --user stop portfolio-news.timer 2>/dev/null || true
cd "$HOME/portfolio-news"
git rev-parse HEAD
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps --force-reinstall .
deploy/run.sh --dry-run
systemctl --user start portfolio-news.timer 2>/dev/null || true
```

The cron schedule needs no pause during a quick upgrade because the application lock rejects overlapping launches. If the upgrade could cross 08:00 Paris time, remove and reinstall it with `deploy/manage-cron.sh remove` and `deploy/manage-cron.sh install`.

Rollback code by replacing `<previous-commit>` with the recorded revision, reinstalling the pinned environment, and running a dry run before restarting the selected scheduler:

```bash
systemctl --user stop portfolio-news.timer 2>/dev/null || true
cd "$HOME/portfolio-news"
git switch --detach <previous-commit>
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps --force-reinstall .
deploy/run.sh --dry-run
systemctl --user start portfolio-news.timer 2>/dev/null || true
```

Database rows are retained indefinitely. Backups should be copied off the Lightsail instance according to the server's existing backup policy.

## Protocol references

- [OpenAI GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/model-guidance?model=gpt-5.6)
- [OpenAI web search controls and source metadata](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Google Sheets value reads](https://developers.google.com/workspace/sheets/api/guides/values)
- [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage)

## Failure behavior

- Google, OpenAI, and Telegram calls use bounded exponential retries for timeouts, rate limits, and transient 5xx responses.
- A Google title/header error or malformed OpenAI structured response fails clearly and sends nothing.
- Individual malformed ticker rows are logged and skipped. A recap that omits a valid holding, cites an unconsulted URL, or returns malformed structured output fails without sending.
- If Telegram fails, the rendered digest remains `prepared`. The next live run sends that exact content before doing any new research, avoiding a second research charge.
- Daily movement paragraphs may recur even when a quote URL is reused. Recent recap context is supplied to the model so stale catalysts are suppressed while daily price coverage remains complete.
