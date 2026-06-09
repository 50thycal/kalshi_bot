"""Configuration loading and validation.

Fail-closed by design:
- Required Kalshi/DB values missing -> Settings() raises -> the worker exits without
  doing anything trade-like.
- KILL_SWITCH missing -> assume the kill switch is active (True).
- BOT_MODE missing/invalid -> default to the safest mode, `scanner`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BotMode = Literal["scanner", "paper", "approval", "live", "weather"]
KalshiEnv = Literal["demo", "production"]

VALID_MODES = ("scanner", "paper", "approval", "live", "weather")
VALID_ENVS = ("demo", "production")

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PRODUCTION_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def normalize_database_url(url: str | None) -> str:
    """Normalize a Postgres URL to the SQLAlchemy + psycopg3 driver form.

    Railway hands out `postgresql://...`; SQLAlchemy 2.0 with psycopg3 needs
    `postgresql+psycopg://...`. Non-postgres URLs (e.g. sqlite for tests) pass
    through unchanged.
    """
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- Kalshi connectivity ---
    kalshi_env: str = "demo"
    kalshi_api_key_id: str
    kalshi_private_key: SecretStr

    # --- Database ---
    database_url: str

    # --- Operating mode / safety ---
    bot_mode: str = "scanner"
    kill_switch: bool = True

    # --- Risk limits ---
    max_order_size: int = 1
    max_market_exposure: float = 25.0
    max_total_exposure: float = 100.0
    max_daily_loss: float = 25.0

    # --- Scan cadence ---
    scan_interval_seconds: int = 300
    run_once: bool = False

    # --- Scanner tuning ---
    target_categories: str = (
        "Economics,Financials,Companies,Climate and Weather,Commodities,Science and Technology"
    )
    target_series_prefixes: str = ""
    max_spread_cents: int = 5
    min_volume: int = 25
    min_open_interest: int = 10
    min_hours_to_close: float = 1.0
    max_markets_per_scan: int = 75
    max_markets_per_category: int = 12
    orderbook_depth: int = 10
    staleness_seconds: int = 120
    log_level: str = "INFO"

    # --- Paper trading (BOT_MODE=paper) ---
    paper_strategies: str = "buy_favorite,momentum,reversion,ladder"
    paper_min_edge_cents: int = 1
    paper_momentum_lookback_hours: float = 6.0
    paper_momentum_project_hours: float = 24.0
    paper_momentum_direction: str = "momentum"
    paper_order_size: int = 1
    paper_starting_bankroll: float = 1000.0
    paper_max_open_positions: int = 50
    paper_max_hold_hours: float = 2.0
    paper_take_profit_cents: int | None = None
    paper_stop_loss_cents: int | None = None
    paper_fees_enabled: bool = True

    # --- Weather mode (BOT_MODE=weather) ---
    weather_top_n: int = 10
    weather_entry_hours: str = "12,8,4"
    weather_strategies: str = "favorite,nws"
    weather_forecast_enabled: bool = True
    nws_user_agent: str = "kalshi-bot (set NWS_USER_AGENT to your app + contact email)"
    paper_abandon_foreign_on_start: bool = True

    @field_validator("paper_momentum_direction", mode="before")
    @classmethod
    def _coerce_momentum_direction(cls, v: object) -> str:
        if v is None:
            return "momentum"
        v = str(v).strip().lower()
        return v if v in ("momentum", "reversion") else "momentum"

    @field_validator("paper_take_profit_cents", "paper_stop_loss_cents", mode="before")
    @classmethod
    def _optional_cents(cls, v: object) -> int | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @field_validator("bot_mode", mode="before")
    @classmethod
    def _coerce_bot_mode(cls, v: object) -> str:
        if v is None:
            return "scanner"
        v = str(v).strip().lower()
        return v if v in VALID_MODES else "scanner"

    @field_validator("kalshi_env", mode="before")
    @classmethod
    def _coerce_env(cls, v: object) -> str:
        if v is None:
            return "demo"
        v = str(v).strip().lower()
        return v if v in VALID_ENVS else "demo"

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: object) -> str:
        return normalize_database_url(None if v is None else str(v))

    @property
    def kalshi_base_url(self) -> str:
        return PRODUCTION_BASE_URL if self.kalshi_env == "production" else DEMO_BASE_URL

    @property
    def private_key_pem(self) -> str:
        # Railway stores multi-line secrets single-line with literal \n.
        raw = self.kalshi_private_key.get_secret_value()
        if "\\n" in raw and "\n" not in raw:
            raw = raw.replace("\\n", "\n")
        return raw

    @property
    def target_category_list(self) -> list[str]:
        return [c.strip().lower() for c in self.target_categories.split(",") if c.strip()]

    @property
    def target_series_prefix_list(self) -> list[str]:
        return [p.strip().upper() for p in self.target_series_prefixes.split(",") if p.strip()]

    @property
    def weather_entry_hours_list(self) -> list[float]:
        out: list[float] = []
        for part in self.weather_entry_hours.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(float(part))
            except ValueError:
                continue
        # Widest window first so the earliest snapshot fires before later ones.
        return sorted(set(out), reverse=True) or [12.0, 8.0, 4.0]

    @property
    def weather_strategy_list(self) -> list[str]:
        valid = ("favorite", "nws")
        out = [s.strip().lower() for s in self.weather_strategies.split(",") if s.strip()]
        return [s for s in out if s in valid] or ["favorite"]

    @property
    def paper_strategy_list(self) -> list[str]:
        valid = ("buy_favorite", "buy_yes", "buy_no", "momentum", "reversion", "ladder")
        out = [s.strip().lower() for s in self.paper_strategies.split(",") if s.strip()]
        return [s for s in out if s in valid] or ["buy_favorite"]

    def redacted_summary(self) -> dict:
        """Config summary safe to log (never includes the private key)."""
        return {
            "kalshi_env": self.kalshi_env,
            "bot_mode": self.bot_mode,
            "kill_switch": self.kill_switch,
            "max_order_size": self.max_order_size,
            "max_market_exposure": self.max_market_exposure,
            "max_total_exposure": self.max_total_exposure,
            "max_daily_loss": self.max_daily_loss,
            "scan_interval_seconds": self.scan_interval_seconds,
            "run_once": self.run_once,
            "target_categories": self.target_category_list,
            "target_series_prefixes": self.target_series_prefix_list,
            "max_spread_cents": self.max_spread_cents,
            "min_volume": self.min_volume,
            "min_open_interest": self.min_open_interest,
            "min_hours_to_close": self.min_hours_to_close,
            "max_markets_per_scan": self.max_markets_per_scan,
            "max_markets_per_category": self.max_markets_per_category,
            "paper_strategies": self.paper_strategy_list,
            "paper_min_edge_cents": self.paper_min_edge_cents,
            "paper_momentum_project_hours": self.paper_momentum_project_hours,
            "paper_momentum_direction": self.paper_momentum_direction,
            "paper_order_size": self.paper_order_size,
            "paper_starting_bankroll": self.paper_starting_bankroll,
            "paper_max_open_positions": self.paper_max_open_positions,
            "paper_max_hold_hours": self.paper_max_hold_hours,
            "paper_take_profit_cents": self.paper_take_profit_cents,
            "paper_stop_loss_cents": self.paper_stop_loss_cents,
            "weather_top_n": self.weather_top_n,
            "weather_entry_hours": self.weather_entry_hours_list,
            "weather_strategies": self.weather_strategy_list,
            "weather_forecast_enabled": self.weather_forecast_enabled,
            "api_key_id_present": bool(self.kalshi_api_key_id),
            "private_key_present": bool(self.private_key_pem),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
