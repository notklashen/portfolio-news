from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_timer_is_persistent_and_paris_weekdays():
    timer = (ROOT / "deploy" / "portfolio-news.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 08:00:00 Europe/Paris" in timer
    assert "Persistent=true" in timer
    assert "AccuracySec=1s" in timer


def test_deployment_service_runs_native_venv_as_dedicated_user():
    service = (ROOT / "deploy" / "portfolio-news.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "User=portfolio-news" in service
    assert "Group=portfolio-news" in service
    assert "EnvironmentFile=/etc/portfolio-news/portfolio-news.env" in service
    assert "ExecStart=/opt/portfolio-news/.venv/bin/python -m portfolio_news run" in service
    assert "StateDirectory=portfolio-news" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service


def test_native_environment_paths_match_service_permissions():
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GOOGLE_CREDENTIALS_FILE=/etc/portfolio-news/google_credentials.json" in environment
    assert "DATABASE_PATH=/var/lib/portfolio-news/portfolio_news.db" in environment
    assert "LOCK_FILE=/var/lib/portfolio-news/portfolio_news.lock" in environment


def test_cron_fallback_checks_paris_wall_clock_explicitly():
    cron = (ROOT / "deploy" / "portfolio-news.cron").read_text(encoding="utf-8")
    assert "TZ=Europe/Paris /bin/date" in cron
    assert '"08:00"' in cron
    assert "CRON_TZ=" not in cron
    assert "/usr/bin/systemctl start --no-block portfolio-news.service" in cron
