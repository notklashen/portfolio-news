# Portfolio News

An outbound-only Telegram bot that reads a Google Sheets portfolio, researches current material news with OpenAI web search, and sends one concise weekday digest. It does not trade, calculate portfolio values, handle Telegram commands, or expose a web service.

The production schedule is 08:00 Monday–Friday in `Europe/Paris`. Delivery history is retained indefinitely in SQLite; only the newest 30 days or 100 stories are included in a research prompt.

## How one run works

1. A non-blocking file lock prevents timer and manual runs from overlapping.
2. Any previously prepared but undelivered digest is retried first, without calling Sheets or OpenAI again.
3. The workbook title is verified as exactly `PORTFOLIO_TRACKER`. Every worksheet is scanned for one case-insensitive `Ticker` cell; zero or multiple matches are fatal.
4. Valid ticker cells beneath that header are trimmed, uppercased, and deduplicated. Malformed rows are logged and skipped.
5. One OpenAI Responses API request researches the interval since the last successfully covered digest, capped at four days. The request uses `gpt-5.6-sol`, low reasoning, low web-search context, structured output, and a domain allowlist.
6. Code rejects non-allowlisted sources, previously delivered canonical URLs, and repeated event keys unless the model marks a genuinely changed summary as a material update.
7. The HTML digest is prepared in SQLite before Telegram is called. Stories become delivered only after Telegram returns a message ID.

An empty result still sends `No material new portfolio news today.` as a health heartbeat.

## Google Sheets setup

1. In a Google Cloud project, enable the Google Sheets API.
2. Create a service account and download its JSON credentials. No Drive scope is needed.
3. Rename the target spreadsheet to exactly `PORTFOLIO_TRACKER`.
4. Share that spreadsheet as **Viewer** with the service account's `client_email` from the JSON file.
5. Copy the spreadsheet ID from the URL segment between `/d/` and `/edit` into `GOOGLE_SPREADSHEET_ID`.
6. Ensure exactly one worksheet contains a cell whose trimmed text is `Ticker`, case-insensitively. Put holdings below that cell in the same column, for example `NASDAQ:GOOG`.

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
- `ADDITIONAL_ALLOWED_DOMAINS`, a comma-separated set of issuer or regional publisher domains
- `MAX_PORTFOLIO_ITEMS` (1–5) and `MAX_MACRO_ITEMS` (0–2)
- `MAX_HISTORY_ITEMS` and `HISTORY_DAYS`
- `MAX_TELEGRAM_CHARS` (default 3,500, hard maximum 4,096)
- API timeouts, retry count, database/lock paths, and `LOG_LEVEL`

For an existing deployment, update any `OPENAI_MODEL` entry in `/etc/portfolio-news/portfolio-news.env` to `gpt-5.6-sol`; an explicit old value overrides the new application default.

The version-controlled base allowlist is in `portfolio_news/sources.py`. It includes Reuters, AP, Bloomberg, FT, WSJ, BBC, CNBC, major US/UK/EU/French regulators and central banks, and international institutions. Extra configured domains receive the same exact-host-or-subdomain enforcement.

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

## Lightsail installation

The following assumes Ubuntu, `python3` reports Python 3.9, and this repository is at `/opt/portfolio-news`. If virtual-environment creation is unavailable, install the Ubuntu package that provides `venv` for the server's Python version first.

```bash
python3 --version
python3 -m venv --help
sudo adduser --system --group --home /opt/portfolio-news --no-create-home portfolio-news
sudo install -d -o root -g portfolio-news -m 0750 /etc/portfolio-news
sudo install -d -o portfolio-news -g portfolio-news -m 0750 /var/lib/portfolio-news
sudo install -o root -g portfolio-news -m 0640 google-service-account.json /etc/portfolio-news/google_credentials.json
sudo install -o root -g portfolio-news -m 0640 .env /etc/portfolio-news/portfolio-news.env
cd /opt/portfolio-news
sudo python3 -m venv .venv
sudo .venv/bin/python -m pip install -r requirements.txt
sudo .venv/bin/python -m pip install --no-deps .
sudo chown -R root:root .venv
sudo chmod -R go-w .venv
```

The service runs as the unprivileged `portfolio-news` user. Configuration and Google credentials are root-owned and readable only by the service group. The installed virtual environment is root-owned, while SQLite and the lock file are writable only beneath `/var/lib/portfolio-news`. The systemd unit also enables filesystem and privilege hardening.

Test first:

```bash
sudo -u portfolio-news /bin/sh -c 'set -a; . /etc/portfolio-news/portfolio-news.env; set +a; cd /var/lib/portfolio-news; exec /opt/portfolio-news/.venv/bin/python -m portfolio_news run --dry-run'
sudo -u portfolio-news /bin/sh -c 'set -a; . /etc/portfolio-news/portfolio-news.env; set +a; cd /var/lib/portfolio-news; exec /opt/portfolio-news/.venv/bin/python -m portfolio_news run'
```

Install and activate the timer:

```bash
sudo install -o root -g root -m 0644 deploy/portfolio-news.service /etc/systemd/system/portfolio-news.service
sudo install -o root -g root -m 0644 deploy/portfolio-news.timer /etc/systemd/system/portfolio-news.timer
sudo systemd-analyze verify /etc/systemd/system/portfolio-news.service /etc/systemd/system/portfolio-news.timer
sudo systemctl daemon-reload
sudo systemd-analyze calendar 'Mon..Fri *-*-* 08:00:00 Europe/Paris'
sudo systemctl enable --now portfolio-news.timer
systemctl list-timers portfolio-news.timer
```

Confirm that `NEXT` is the next weekday at 08:00 Paris time. `Persistent=true` makes a missed firing run after the host resumes.

If the installed systemd cannot parse the timezone-qualified calendar, do not enable the timer. Install the fallback with `sudo install -o root -g root -m 0644 deploy/portfolio-news.cron /etc/cron.d/portfolio-news`. It evaluates Paris wall time explicitly each minute and asks systemd to start the same hardened service, so it remains DST-aware without relying on host timezone or `CRON_TZ` support. Inspect the next execution in the cron logs.

## Operations

Manual run and log inspection:

```bash
sudo systemctl start portfolio-news.service
sudo journalctl -u portfolio-news.service -n 200 --no-pager
sudo journalctl -u portfolio-news.service -f
```

The application emits JSON logs. Known API keys and bot tokens are redacted; do not place secrets inside publisher-domain or path settings.

Back up SQLite while no run is active:

```bash
sudo systemctl stop portfolio-news.timer
sudo systemctl stop portfolio-news.service
sudo install -d -o root -g root -m 0700 /var/backups/portfolio-news
sudo /opt/portfolio-news/.venv/bin/python -c 'import datetime,sqlite3; name="/var/backups/portfolio-news/portfolio-news-"+datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".db"; source=sqlite3.connect("/var/lib/portfolio-news/portfolio_news.db"); target=sqlite3.connect(name); source.backup(target); target.close(); source.close()'
sudo systemctl start portfolio-news.timer
```

Restore only with the timer and service stopped, and retain the current database file until the restored copy has been verified. SQLite's online backup API produces a consistent database even when WAL mode is enabled.

Review cumulative OpenAI usage recorded per run:

```bash
sudo -u portfolio-news /opt/portfolio-news/.venv/bin/python -c 'import sqlite3; db=sqlite3.connect("/var/lib/portfolio-news/portfolio_news.db"); print(db.execute("select count(*),sum(input_tokens),sum(output_tokens),sum(web_search_calls) from (select input_tokens,output_tokens,web_search_calls from prepared_digests union all select input_tokens,output_tokens,web_search_calls from runs where dry_run=1 and status=\"dry_run\")").fetchone())'
```

Upgrade with a recorded rollback point:

```bash
sudo systemctl stop portfolio-news.timer
cd /opt/portfolio-news
git rev-parse HEAD
git pull --ff-only
sudo .venv/bin/python -m pip install -r requirements.txt
sudo .venv/bin/python -m pip install --no-deps --force-reinstall .
sudo -u portfolio-news /bin/sh -c 'set -a; . /etc/portfolio-news/portfolio-news.env; set +a; cd /var/lib/portfolio-news; exec /opt/portfolio-news/.venv/bin/python -m portfolio_news run --dry-run'
sudo systemctl start portfolio-news.timer
```

Rollback code by replacing `<previous-commit>` with the recorded revision, reinstalling the pinned environment, and running a dry run before re-enabling the timer:

```bash
sudo systemctl stop portfolio-news.timer
cd /opt/portfolio-news
git switch --detach <previous-commit>
sudo .venv/bin/python -m pip install -r requirements.txt
sudo .venv/bin/python -m pip install --no-deps --force-reinstall .
sudo -u portfolio-news /bin/sh -c 'set -a; . /etc/portfolio-news/portfolio-news.env; set +a; cd /var/lib/portfolio-news; exec /opt/portfolio-news/.venv/bin/python -m portfolio_news run --dry-run'
sudo systemctl start portfolio-news.timer
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
- Individual malformed ticker rows and rejected research stories are logged without discarding valid holdings or valid stories.
- If Telegram fails, the rendered digest remains `prepared`. The next live run sends that exact content before doing any new research, avoiding a second research charge.
- URLs are canonicalized before exact deduplication. Stable event keys and recent summaries suppress the same event across publishers; a repeat requires both `material_update=true` and changed relevance text.
