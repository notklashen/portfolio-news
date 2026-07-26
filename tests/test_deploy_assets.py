from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_timer_is_persistent_and_paris_weekdays():
    timer = (ROOT / "deploy" / "portfolio-news.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 08:00:00 Europe/Paris" in timer
    assert "Persistent=true" in timer
    assert "AccuracySec=1s" in timer


def test_deployment_service_runs_from_the_calling_users_home():
    service = (ROOT / "deploy" / "portfolio-news.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "User=" not in service
    assert "Group=" not in service
    assert "EnvironmentFile=%h/.config/portfolio-news/portfolio-news.env" in service
    assert "ExecStart=%h/portfolio-news/.venv/bin/python -m portfolio_news run" in service
    assert "NoNewPrivileges=true" in service
    assert "CapabilityBoundingSet=" in service
    assert "network-online.target" not in service
    assert "/opt/" not in service
    assert "/etc/portfolio-news" not in service
    assert "/var/lib/portfolio-news" not in service


def test_environment_defaults_use_user_owned_paths():
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "GOOGLE_CREDENTIALS_FILE=~/.config/portfolio-news/google_credentials.json" in environment
    assert "DATABASE_PATH=~/.local/state/portfolio-news/portfolio_news.db" in environment
    assert "LOCK_FILE=~/.local/state/portfolio-news/portfolio_news.lock" in environment


def test_cron_fallback_checks_paris_wall_clock_explicitly():
    cron = (ROOT / "deploy" / "portfolio-news.cron").read_text(encoding="utf-8")
    assert "TZ=Europe/Paris /bin/date" in cron
    assert '"08:00"' in cron
    assert "CRON_TZ=" not in cron
    assert '"$HOME/portfolio-news/deploy/run.sh"' in cron
    assert " systemctl " not in cron
    assert "* * * * * root " not in cron


def test_rootless_runner_loads_private_user_environment():
    runner = (ROOT / "deploy" / "run.sh").read_text(encoding="utf-8")
    assert "$HOME/.config/portfolio-news/portfolio-news.env" in runner
    assert "$HOME/portfolio-news" in runner
    assert 'exec "$PYTHON" -m portfolio_news run "$@"' in runner


def test_cron_manager_preserves_unmanaged_entries():
    manager = (ROOT / "deploy" / "manage-cron.sh").read_text(encoding="utf-8")
    assert "crontab -l" in manager
    assert 'CRON_TEMPLATE="$SCRIPT_DIR/portfolio-news.cron"' in manager
    assert "sudo" not in manager
