from __future__ import annotations

import pytest
from pydantic import ValidationError

from kalshi_bot.config import Settings, normalize_database_url


def test_missing_required_fails(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_kill_switch_defaults_true(base_env, monkeypatch):
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    assert Settings(_env_file=None).kill_switch is True


def test_bot_mode_defaults_scanner(base_env, monkeypatch):
    monkeypatch.delenv("BOT_MODE", raising=False)
    assert Settings(_env_file=None).bot_mode == "scanner"


def test_bot_mode_invalid_is_coerced_to_scanner(base_env, monkeypatch):
    monkeypatch.setenv("BOT_MODE", "definitely-not-a-mode")
    assert Settings(_env_file=None).bot_mode == "scanner"


def test_env_invalid_is_coerced_to_demo(base_env, monkeypatch):
    monkeypatch.setenv("KALSHI_ENV", "staging")
    s = Settings(_env_file=None)
    assert s.kalshi_env == "demo"
    assert s.kalshi_base_url.startswith("https://demo-api.kalshi.co")


def test_production_base_url(base_env, monkeypatch):
    monkeypatch.setenv("KALSHI_ENV", "production")
    assert "elections.kalshi.com" in Settings(_env_file=None).kalshi_base_url


def test_database_url_normalization():
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql+psycopg://x") == "postgresql+psycopg://x"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"
    assert normalize_database_url("") == ""


def test_redacted_summary_excludes_secrets(settings):
    summary = settings.redacted_summary()
    assert "kalshi_private_key" not in summary
    assert "private_key" not in summary
    assert summary["private_key_present"] is True
    assert summary["api_key_id_present"] is True


def test_escaped_newline_private_key(base_env, monkeypatch, rsa_keypair):
    _, pem = rsa_keypair
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem.replace("\n", "\\n"))
    s = Settings(_env_file=None)
    assert "\n" in s.private_key_pem
    assert "\\n" not in s.private_key_pem
