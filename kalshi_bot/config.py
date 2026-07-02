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

VALID_MODES = ("scanner", "paper", "approval", "live", "weather", "mmsell")
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

    # --- Market-making SELL book (BOT_MODE=mmsell) ---
    # Forward-tests the backtested maker edge: rest an ASK to sell yes on overpriced cheap/
    # underdog contracts (= buy NO at the no-bid, the maker price) and HOLD to settlement.
    # Entry band is on the yes midpoint; the backtest edge lives ~5-40c (0-3c pennies die to
    # fees). Hold-to-settlement is optimal (exit sweep: TP/SL only hurt) so tp/sl default off.
    mmsell_entry_lo_cents: int = 5
    mmsell_entry_hi_cents: int = 40
    mmsell_min_volume: float = 100.0
    mmsell_min_hours_to_close: float = 1.0
    mmsell_max_hours_to_close: float = 336.0     # 14 days — bound how long capital is tied up
    mmsell_top_events: int = 150                 # scan cap per cycle (liquid events, by volume)
    mmsell_max_open_positions: int = 200         # diversification is the real risk control
    mmsell_skip_series: str = "KXMVE,KXHIGH,KXLOW"  # skip parlays + weather (its own book)
    # Ride-along: run the mmsell PAPER book inside the weather/live cycle (throttled), so it
    # collects alongside the weather books without a disruptive mode switch or any real money.
    mmsell_paper_enabled: bool = False
    mmsell_interval_minutes: float = 30.0        # how often the ride-along entry scan runs

    # --- Weather mode (BOT_MODE=weather) ---
    weather_top_n: int = 10
    weather_entry_hours: str = "20,14,8"
    weather_strategies: str = "favorite,nws,cal"
    weather_forecast_enabled: bool = True
    nws_user_agent: str = "kalshi-bot (set NWS_USER_AGENT to your app + contact email)"
    # HRRR (NOAA's hourly, high-res, <=48h CONUS model) point forecast via Open-Meteo
    # (model id ncep_hrrr_conus) — stored in weather_forecasts with source='openmeteo_hrrr'
    # ALONGSIDE the NWS forecast, and graded head-to-head vs NWS/market in the validation
    # dataset. COLLECT + GRADE ONLY this round: no book trades on it yet. HRRR updates
    # hourly; refresh at most every N minutes.
    weather_hrrr_enabled: bool = True
    weather_hrrr_interval_minutes: float = 15.0
    paper_abandon_foreign_on_start: bool = True
    # `cal` book: per-city forecast bias correction learned from settled history.
    # offset = mean(actual_high - forecast), shrunk toward 0 by n/(n+shrinkage) so a
    # couple of events don't overcorrect; only cities with >= min_events contribute.
    weather_bias_shrinkage: float = 3.0
    weather_bias_min_events: int = 1
    # Daily LOW temperature markets (KXLOWT*): track + trade the same books in parallel.
    # WEATHER_LOW_SERIES overrides per-city series tickers, e.g. "AUS=KXLOWTAUSTIN".
    weather_track_lows: bool = True
    weather_low_series: str = ""
    # Intraday station observations (running max/min so far today at the settlement
    # station) — stored in weather_observations, refreshed at most every N minutes.
    weather_obs_enabled: bool = True
    weather_obs_interval_minutes: float = 15.0
    # Open-Meteo ensemble members (the forecast *distribution*) — stored in
    # weather_ensembles. Models update ~6-hourly; refresh at most every N minutes.
    weather_ensemble_enabled: bool = True
    # GFS + ECMWF + ICON-EPS + GEM-EPS: wider model disagreement = better sigma for the
    # `dist` bucket model. The client fails soft per model, so an unrecognized id just logs
    # and is skipped (a safe way to verify ids on first run).
    weather_ensemble_models: str = "gfs_seamless,ecmwf_ifs025,icon_seamless,gem_global"
    weather_ensemble_interval_minutes: float = 60.0
    # Full bucket-ladder price snapshots (the market's implied distribution) — stored
    # in weather_bucket_snapshots at most every N minutes per event. The ladder reads
    # the markets the cycle already fetched (no extra API calls), so this can run as
    # fast as the scan cycle; finer paths make the exit/entry replay studies sharper.
    weather_ladder_interval_minutes: float = 5.0
    # Polymarket cross-market signal. Polymarket runs the same daily temperature
    # markets; the `weather_pm` book trades Kalshi toward Polymarket's implied price,
    # but ONLY for cities where the settlement station matches Kalshi (verified
    # LAX/MIA/AUS). Polymarket prices are read-only signal — we never trade Polymarket
    # (geofenced). Stored separately in polymarket_snapshots.
    weather_polymarket_enabled: bool = True
    weather_pm_cities: str = "LAX,MIA,AUS"
    weather_pm_interval_minutes: float = 5.0
    # Per-city entry-window book (`weather_cwin`): the backfill-validated optimal
    # hours-to-close for the HIGH favorite per city (h18 for CHI/LAX/DEN won an
    # out-of-sample holdout). Buys the favorite once at that city's window.
    weather_city_window_enabled: bool = True
    weather_city_windows: str = "CHI:18,LAX:18,DEN:18,NYC:10,MIA:24,AUS:24,PHIL:10"
    # Per-city favorite PRICE-BAND book (`weather_favband`): buy the HIGH favorite at the
    # normal entry windows only when its implied price sits in a per-city band. The LAX
    # favorite is underpriced at 50-70c but OVERpriced >70c (over-paying for overshoot
    # risk) — buying only in-band survived an out-of-sample date split AND the h20/h14
    # windows in the backfill calibration study. Format: CITY:lo-hi cents.
    weather_favband_enabled: bool = True
    weather_favband_bands: str = "LAX:50-70"
    # Obs-confirmed late entry (`weather_obs` / `weather_low_obs`): after the local
    # cutoff hour the day's high/low has usually formed, so the station's running
    # max/min is a near-locked bound the market lags. Buy the bucket containing it
    # once, if its ask is still <= the cap (the entry study showed +EV late & cheap).
    weather_obs_entry_enabled: bool = True
    weather_obs_high_after_hour: int = 16  # local hour; the high typically forms by ~3-4pm
    weather_obs_low_after_hour: int = 7    # the overnight low typically forms by ~dawn
    weather_obs_ask_cap: float = 90.0      # skip if the running-extreme bucket is already rich
    # Distribution edge book (`weather_dist` / `weather_low_dist`): price every bucket off
    # the stored Open-Meteo ensemble (a Gaussian kernel per member, blended across models),
    # and buy the single bucket whose model probability most beats its ask. sigma is the
    # kernel width (deg F) for forecast error beyond the ensemble spread — kept modest so
    # the model distribution stays tighter than the market's overdispersed ladder (#6).
    weather_dist_enabled: bool = True
    weather_dist_sigma: float = 1.5
    weather_dist_min_edge_cents: float = 5.0
    # Consensus / layered book (`weather_con` / `weather_low_con`): make the independent
    # signal families (fc=hrrr|nws, ens, obs, pm) CONVERGE before trading instead of
    # following one. Offline-validated two-mode rule (scripts/weather_consensus_study.py):
    #  - HIGH at early windows -> skill-weighted blend (obs/pm weighted above forecasts),
    #    trade only when it DEVIATES from the favorite (cheaper, model-preferred, +EV);
    #  - LOW / HIGH-late -> trade only a near-unanimous K agreement that lands ON the
    #    favorite (a high-confidence near-lock filter), else skip.
    weather_consensus_enabled: bool = True
    weather_consensus_tol: int = 1                      # bucket tolerance for "agree" (+/-)
    weather_consensus_weights: str = "fc=1,ens=1,obs=2,pm=2"
    weather_consensus_early_windows: str = "20,14"      # high windows that use the deviate mode
    weather_consensus_early_min_mass: float = 3.0       # high-early weighted-mass threshold
    weather_consensus_confirm_k: int = 4                # low/late: families agreeing on the favorite
    # Kalshi history backfill (separate backfill_* tables; provenance never mixes with
    # the live-collected snapshots). Runs as a bounded chunk per cycle inside the
    # weather worker — the only place holding Kalshi credentials + a writable DB URL.
    weather_backfill_enabled: bool = True
    weather_backfill_days: float = 120.0
    weather_backfill_markets_per_cycle: int = 40
    weather_backfill_period_minutes: int = 60  # candle granularity: 1, 60 or 1440
    # Persisted forecast->settlement validation dataset (weather_forecast_outcomes).
    # Materialized at settlement by replaying the raw live-collected tables; a bounded
    # backfill (settled events with no rows yet) drains historical settlements gradually,
    # off the trading path. max_htc caps how far before close a cycle is kept (bounds rows).
    weather_validation_enabled: bool = True
    weather_validation_events_per_cycle: int = 25
    weather_validation_max_htc: float = 24.0

    # --- Live real-money execution (ALL default OFF; the layer is inert until an operator
    # flips BOT_MODE=live + KILL_SWITCH=false + LIVE_ENABLED=true AND lists a strategy). The
    # client also self-guards place_order on mode+kill_switch, so this is defense in depth.
    live_enabled: bool = False
    live_strategies: str = ""               # allowlist of strategy prefixes; empty = inert
    live_cities: str = ""                   # restrict to these city codes (empty = all)
    live_windows: str = ""                  # restrict to these entry windows hN (empty = all)
    live_cells: str = ""                    # precise (book:CITY:window) allowlist; supersedes cities/windows
    live_entry_grace_hours: float = 2.0     # skip a window entry if hours-to-close is >this past it
    live_entry_style: str = "marketable"    # "marketable" (limit @ ask) | "passive" (rest below)
    live_passive_offset_cents: int = 2      # passive: rest this many cents below the ask
    live_entry_slippage_cents: int = 2      # marketable: cross up to this many cents above the ask
    #                                         so a thin best-ask level can't shrink the dollar-cap
    #                                         size; count is capped to depth within ask+this band.
    live_order_timeout_seconds: int = 600   # cancel an unfilled passive order after this long
    live_max_order_dollars: float = 5.0     # per-order dollar cap -> qty = floor(cap / price)
    live_exit_mode: str = "settlement"      # "settlement" (hold) | "tp_sl" (TP/SL/break-even)
    live_take_profit_cents: int | None = None
    live_stop_loss_cents: int | None = None
    live_break_even_arm_cents: int | None = None
    # Per-entry-window take-profit (tp_sl mode), e.g. "20:5,14:20": the h20 entry scalps a tight
    # +5c, the h14 (higher-conviction) entry runs to +20c. A window listed here is TP-ONLY (no
    # stop — stops whipsaw these high-win favorites); windows not listed fall back to the global
    # take_profit/stop/break-even above. Validated per-window on the LAX favorite.
    live_take_profit_by_window: str = ""
    # Cap real exposure to ONE open position per event (city-day): skip a later-window entry while
    # an earlier-window position on the same event is still open. With the tight h20 TP it usually
    # closes before h14, freeing h14 to run; if h20 is stuck, h14 is skipped (no doubling down).
    live_one_position_per_event: bool = False
    live_kill_on_daily_loss: bool = True    # self-trip entries when realized_today <= -max_daily_loss
    live_shape_probe: bool = False          # log live API response shapes once at startup (read-only)
    # Hardened exit (tp_sl mode): re-attempt the close until the position is flat, escalating
    # the buy-NO price. slippage_cents crosses deeper on re-attempts; market fallback is a
    # best-effort last resort (Kalshi market-order fields unconfirmed, default OFF); max_attempts
    # bounds re-tries per position/day, then it holds to settlement.
    live_exit_slippage_cents: int = 0
    live_exit_use_market_fallback: bool = False
    live_exit_max_attempts: int = 3
    # The bucket close uses Kalshi's v1 user-scoped order endpoint
    # (POST /v1/users/{user_id}/orders) — the one the web app uses — because the v2
    # /portfolio/orders endpoint rejects closes on range-bucket markets. Needs the account's
    # user_id (an account UUID, not a secret; set via env). Empty -> v1 close disabled.
    live_user_id: str = ""
    # When true, entries size by a FRACTIONAL contract count (count_fp = dollars / price) so a
    # position costs the dollar cap precisely, instead of flooring to whole shares. Default OFF
    # (whole-share sizing) — flipping it on changes live entry sizing, so it's opt-in.
    live_fractional: bool = False
    # One-shot isolated PROBE for fractional buy/sell verification, gated here (empty = off).
    # NEVER touches the strategy pipeline. Directives:
    #   "buy:<ticker>:<dollars>" -> a fractional v2 buy (count_fp = dollars/ask)
    #   "close:<ticker_prefix>"  -> targeted v1 close of ONLY positions matching that prefix
    live_probe: str = ""

    @field_validator("paper_momentum_direction", mode="before")
    @classmethod
    def _coerce_momentum_direction(cls, v: object) -> str:
        if v is None:
            return "momentum"
        v = str(v).strip().lower()
        return v if v in ("momentum", "reversion") else "momentum"

    @field_validator(
        "paper_take_profit_cents", "paper_stop_loss_cents",
        "live_take_profit_cents", "live_stop_loss_cents", "live_break_even_arm_cents",
        mode="before",
    )
    @classmethod
    def _optional_cents(cls, v: object) -> int | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @field_validator("live_entry_style", mode="before")
    @classmethod
    def _coerce_live_entry_style(cls, v: object) -> str:
        if v is None:
            return "marketable"
        v = str(v).strip().lower()
        return v if v in ("marketable", "passive") else "marketable"

    @field_validator("live_exit_mode", mode="before")
    @classmethod
    def _coerce_live_exit_mode(cls, v: object) -> str:
        if v is None:
            return "settlement"
        v = str(v).strip().lower()
        return v if v in ("settlement", "tp_sl") else "settlement"

    @field_validator("live_exit_slippage_cents", mode="before")
    @classmethod
    def _coerce_exit_slippage(cls, v: object) -> int:
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    @field_validator("live_exit_max_attempts", mode="before")
    @classmethod
    def _coerce_exit_max_attempts(cls, v: object) -> int:
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 3

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
        valid = ("favorite", "nws", "cal")
        out = [s.strip().lower() for s in self.weather_strategies.split(",") if s.strip()]
        return [s for s in out if s in valid] or ["favorite"]

    @property
    def weather_low_series_map(self) -> dict[str, str]:
        """Optional per-city low-series overrides: "NYC=KXLOWTNYC,AUS=KXLOWTAUSTIN"."""
        out: dict[str, str] = {}
        for part in self.weather_low_series.split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            code, ticker = part.split("=", 1)
            if code.strip() and ticker.strip():
                out[code.strip().upper()] = ticker.strip().upper()
        return out

    @property
    def weather_ensemble_model_list(self) -> list[str]:
        return [p.strip() for p in self.weather_ensemble_models.split(",") if p.strip()]

    @property
    def live_strategy_list(self) -> list[str]:
        """Allowlist of strategy prefixes permitted to place real orders. Book-agnostic:
        no whitelist filter — empty means nothing trades live."""
        return [s.strip() for s in self.live_strategies.split(",") if s.strip()]

    @property
    def live_city_list(self) -> list[str]:
        """Optional city-code filter for live orders (empty = all cities)."""
        return [c.strip().upper() for c in self.live_cities.split(",") if c.strip()]

    @property
    def live_window_list(self) -> list[int]:
        """Optional entry-window filter for live orders, in hours (empty = all windows)."""
        out: list[int] = []
        for part in self.live_windows.split(","):
            part = part.strip().lower().lstrip("h")
            if part:
                try:
                    out.append(int(part))
                except ValueError:
                    continue
        return out

    @property
    def live_cell_list(self) -> list[tuple[str, str, int]]:
        """Precise per-cell live allowlist: a list of (book_prefix, CITY, window) tuples parsed
        from `live_cells` ("weather_fav:DEN:20,weather_low_fav:NYC:20,..."). When non-empty it is
        the exact set of cells permitted to trade live and SUPERSEDES live_cities/live_windows —
        so a mix like high-fav on DEN/LAX plus low-fav on NYC/PHIL can be expressed exactly,
        without the coarse cross-product enabling unwanted cells."""
        out: list[tuple[str, str, int]] = []
        for part in self.live_cells.split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split(":")
            if len(bits) != 3:
                continue
            book, city, win = bits[0].strip(), bits[1].strip().upper(), bits[2].strip().lower().lstrip("h")
            if not book or not city or not win:
                continue
            try:
                out.append((book, city, int(win)))
            except ValueError:
                continue
        return out

    @property
    def live_tp_by_window_map(self) -> dict[int, int]:
        """Per-window take-profit cents parsed from `live_take_profit_by_window` ("20:5,14:20")."""
        out: dict[int, int] = {}
        for part in self.live_take_profit_by_window.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            win, tp = part.split(":", 1)
            try:
                out[int(win.strip().lower().lstrip("h"))] = int(tp.strip())
            except ValueError:
                continue
        return out

    @property
    def weather_pm_city_list(self) -> list[str]:
        return [p.strip().upper() for p in self.weather_pm_cities.split(",") if p.strip()]

    @property
    def weather_city_window_map(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for tok in self.weather_city_windows.split(","):
            if ":" in tok:
                city, _, hrs = tok.partition(":")
                try:
                    out[city.strip().upper()] = float(hrs)
                except ValueError:
                    continue
        return out

    @property
    def weather_favband_map(self) -> dict[str, tuple[float, float]]:
        """CITY -> (low_cents, high_cents) inclusive-low/exclusive-high price band for the
        favband book. Parses "LAX:50-70,AUS:..."; skips malformed or inverted tokens."""
        out: dict[str, tuple[float, float]] = {}
        for tok in self.weather_favband_bands.split(","):
            city, _, rng = tok.partition(":")
            lo_s, _, hi_s = rng.partition("-")
            try:
                lo, hi = float(lo_s), float(hi_s)
            except ValueError:
                continue
            if city.strip() and lo < hi:
                out[city.strip().upper()] = (lo, hi)
        return out

    @property
    def paper_strategy_list(self) -> list[str]:
        valid = ("buy_favorite", "buy_yes", "buy_no", "momentum", "reversion", "ladder")
        out = [s.strip().lower() for s in self.paper_strategies.split(",") if s.strip()]
        return [s for s in out if s in valid] or ["buy_favorite"]

    @property
    def mmsell_skip_series_list(self) -> list[str]:
        return [s.strip().upper() for s in self.mmsell_skip_series.split(",") if s.strip()]

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
            "mmsell_band_cents": [self.mmsell_entry_lo_cents, self.mmsell_entry_hi_cents],
            "mmsell_min_volume": self.mmsell_min_volume,
            "mmsell_htc_hours": [self.mmsell_min_hours_to_close, self.mmsell_max_hours_to_close],
            "mmsell_top_events": self.mmsell_top_events,
            "mmsell_max_open_positions": self.mmsell_max_open_positions,
            "mmsell_paper_enabled": self.mmsell_paper_enabled,
            "mmsell_interval_minutes": self.mmsell_interval_minutes,
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
            "weather_hrrr_enabled": self.weather_hrrr_enabled,
            "weather_hrrr_interval_minutes": self.weather_hrrr_interval_minutes,
            "weather_bias_shrinkage": self.weather_bias_shrinkage,
            "weather_track_lows": self.weather_track_lows,
            "weather_obs_enabled": self.weather_obs_enabled,
            "weather_ensemble_enabled": self.weather_ensemble_enabled,
            "weather_ensemble_models": self.weather_ensemble_model_list,
            "weather_dist_enabled": self.weather_dist_enabled,
            "weather_dist_sigma": self.weather_dist_sigma,
            "weather_consensus_enabled": self.weather_consensus_enabled,
            "weather_consensus_confirm_k": self.weather_consensus_confirm_k,
            "weather_dist_min_edge_cents": self.weather_dist_min_edge_cents,
            "weather_validation_enabled": self.weather_validation_enabled,
            "weather_validation_events_per_cycle": self.weather_validation_events_per_cycle,
            "weather_validation_max_htc": self.weather_validation_max_htc,
            "live_enabled": self.live_enabled,
            "live_strategies": self.live_strategy_list,
            "live_cities": self.live_city_list,
            "live_windows": self.live_window_list,
            "live_cells": [f"{b}:{c}:{w}" for b, c, w in self.live_cell_list],
            "live_entry_grace_hours": self.live_entry_grace_hours,
            "live_entry_style": self.live_entry_style,
            "live_entry_slippage_cents": self.live_entry_slippage_cents,
            "live_exit_mode": self.live_exit_mode,
            "live_take_profit_by_window": self.live_tp_by_window_map,
            "live_one_position_per_event": self.live_one_position_per_event,
            "live_exit_slippage_cents": self.live_exit_slippage_cents,
            "live_exit_use_market_fallback": self.live_exit_use_market_fallback,
            "live_exit_max_attempts": self.live_exit_max_attempts,
            "live_max_order_dollars": self.live_max_order_dollars,
            "live_user_id_present": bool(self.live_user_id),
            "api_key_id_present": bool(self.kalshi_api_key_id),
            "private_key_present": bool(self.private_key_pem),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
