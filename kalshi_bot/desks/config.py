"""Settings for the desk service, intentionally independent of BOT_MODE and LIVE_*."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DESKS_", extra="ignore", env_file=None)

    database_url: SecretStr
    round_id: str = Field(default="desks-round-1", pattern=r"^[A-Za-z0-9_-]{1,100}$")
    # How a NEW round's books are funded when earlier rounds exist in the database:
    # carry = each desk keeps its prior book cash (capped at $30); fresh = $30 each.
    new_round_bankroll: Literal["carry", "fresh"] = "carry"
    operator_token: SecretStr
    chatgpt_token: SecretStr
    claude_token: SecretStr
    host: str = "127.0.0.1"
    port: int = Field(default=8090, ge=1, le=65535)
    live_enabled: bool = False
    account_mode: Literal["isolated", "shared_primary"] = "isolated"
    shared_ownership_url: SecretStr = SecretStr("")
    shared_account_namespace: str = "kalshi-primary"
    existing_workers_isolated: bool = False
    external_runners_verified: bool = False
    research_mode: Literal["scheduled", "session"] = "scheduled"
    alert_webhook_url: SecretStr = SecretStr("")
    alert_mode: Literal["webhook", "session"] = "webhook"
    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"
    chatgpt_subaccount: int = Field(default=0, ge=0, le=63)
    claude_subaccount: int = Field(default=0, ge=0, le=63)
    chatgpt_kalshi_key_id: SecretStr = SecretStr("")
    chatgpt_kalshi_private_key: SecretStr = SecretStr("")
    claude_kalshi_key_id: SecretStr = SecretStr("")
    claude_kalshi_private_key: SecretStr = SecretStr("")
    research_interval_seconds: int = Field(default=3600, ge=300, le=86400)
    research_timezone: str = "UTC"
    chatgpt_research_schedule: str = "interval"
    claude_research_schedule: str = "interval"
    tick_seconds: int = Field(default=15, ge=1, le=60)
    # DEC-024: generous, configurable research limits that protect the service, not usage.
    max_sources_per_job: int = Field(default=50, ge=1, le=500)
    research_lease_minutes: int = Field(default=60, ge=15, le=240)
    monthly_research_budget_usd: Decimal = Field(default=Decimal(0), ge=0)
    chatgpt_provider: Literal["external", "openai"] = "external"
    claude_provider: Literal["external", "anthropic"] = "external"
    chatgpt_model: str = ""
    claude_model: str = ""
    chatgpt_model_key: SecretStr = SecretStr("")
    claude_model_key: SecretStr = SecretStr("")
    chatgpt_input_usd_per_million: Decimal = Field(default=Decimal(0), ge=0)
    chatgpt_output_usd_per_million: Decimal = Field(default=Decimal(0), ge=0)
    claude_input_usd_per_million: Decimal = Field(default=Decimal(0), ge=0)
    claude_output_usd_per_million: Decimal = Field(default=Decimal(0), ge=0)

    @model_validator(mode="after")
    def validate_isolation(self):
        try:
            ZoneInfo(self.research_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("research timezone must be an IANA time zone") from None
        for desk in ("chatgpt", "claude"):
            value = getattr(self, f"{desk}_research_schedule")
            if value in {"interval", "disabled"}:
                continue
            windows = value.split(",")
            if not windows or len(set(windows)) != len(windows):
                raise ValueError(f"{desk} research schedule must contain unique HH:MM windows")
            for window in windows:
                try:
                    hour, minute = window.split(":")
                    valid = len(hour) == 2 and len(minute) == 2 and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
                except (ValueError, AttributeError):
                    valid = False
                if not valid:
                    raise ValueError(f"{desk} research schedule must be interval, disabled, or comma-separated HH:MM windows")
        if self.alert_mode == "session" and self.research_mode != "session":
            raise ValueError("session-only alerts require app-session research")
        tokens = [getattr(self, f"{role}_token").get_secret_value()
                  for role in ("operator", "chatgpt", "claude")]
        if any(len(t) < 32 for t in tokens) or len(set(tokens)) != 3:
            raise ValueError("three distinct authentication tokens of at least 32 characters required")
        allowed = {
            "https://external-api.kalshi.com/trade-api/v2",
            "https://demo-api.kalshi.co/trade-api/v2",
        }
        if self.kalshi_base_url not in allowed:
            raise ValueError("unsupported Kalshi host")
        if self.research_mode == "session" and (
                self.chatgpt_provider != "external" or self.claude_provider != "external"):
            raise ValueError("session mode requires external app sessions for both desks")
        if self.live_enabled:
            if not self.existing_workers_isolated:
                raise ValueError("existing worker isolation must be independently verified")
            accounts = [self.chatgpt_subaccount, self.claude_subaccount]
            if self.account_mode == "shared_primary":
                if accounts != [0, 0] or not self.shared_ownership_url.get_secret_value():
                    raise ValueError("shared desks require primary account and ownership database")
            elif 0 in accounts or len(set(accounts)) != 2:
                raise ValueError("live desks require two distinct non-primary subaccounts")
            keys = [self.chatgpt_kalshi_key_id.get_secret_value(),
                    self.claude_kalshi_key_id.get_secret_value()]
            if not all(keys) or (self.account_mode == "isolated" and len(set(keys)) != 2):
                raise ValueError("live desks require distinct restricted Kalshi keys")
            if not all(getattr(self, f"{d}_kalshi_private_key").get_secret_value()
                       for d in ("chatgpt", "claude")):
                raise ValueError("live desks require dedicated signing credentials")
        return self

    def static_blockers(self) -> list[str]:
        blockers = []
        if not self.live_enabled:
            blockers.append("live_execution_disabled")
        if not self.existing_workers_isolated:
            blockers.append("existing_worker_subaccount_isolation_unverified")
        accounts = [self.chatgpt_subaccount, self.claude_subaccount]
        if self.account_mode == "shared_primary":
            if accounts != [0, 0]:
                blockers.append("shared_primary_account_required")
            if not self.shared_ownership_url.get_secret_value():
                blockers.append("shared_ownership_database_required")
        elif 0 in accounts or len(set(accounts)) != 2:
            blockers.append("two_distinct_non_primary_subaccounts_required")
        for desk in ("chatgpt", "claude"):
            if not getattr(self, f"{desk}_kalshi_key_id").get_secret_value():
                blockers.append(f"{desk}_exchange_credentials_missing")
            if not getattr(self, f"{desk}_kalshi_private_key").get_secret_value():
                blockers.append(f"{desk}_exchange_signing_key_missing")
            if self.research_mode == "session":
                continue  # App sessions prove readiness through completed leased research.
            if getattr(self, f"{desk}_provider") == "external":
                if not self.external_runners_verified:
                    blockers.append(f"{desk}_unattended_runner_unverified")
            elif self.monthly_research_budget_usd <= 0:
                blockers.append(f"{desk}_paid_research_budget_not_authorized")
            elif not getattr(self, f"{desk}_model_key").get_secret_value():
                blockers.append(f"{desk}_model_credentials_missing")
        return blockers
