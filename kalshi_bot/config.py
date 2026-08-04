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

VALID_MODES = ("scanner", "paper", "approval", "live", "weather", "mmsell", "evo")
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

    # --- settlement-date concentration cap (docs/MMSELL_SEASONAL_FORECAST.md "Reading 3") ---
    # mmsell's risk model IS diversification: many small positions, none individually able to
    # sink the book. That model silently breaks when a book's positions cluster on one
    # SETTLEMENT DATE against a shared driver (an election night; measured live 2026-08-03: 55%
    # of the live cap already settled on a single August date, no election required). 60
    # positions settling together is closer to one position at 60x size than to a diversified
    # book. Applied PER BOOK, evaluated against that book's own max_open_positions (paper's 200
    # or a twin's live-sized 60) — the same asymmetry the position cap already uses.
    mmsell_settlement_cap_enabled: bool = True
    mmsell_settlement_cap_pct: float = 0.25      # >=25% of a book's own cap on one date -> skip
    # Regimes where positions settle on a shared driver, so the cap above is not enough on its
    # own: a governor's race and a senate race both breaking the same way on election night is
    # ONE outcome wearing many tickers. Within a single EVENT the rungs are mutually exclusive
    # (at most one loses), which is a real hedge; across events that protection disappears, so
    # this second cap counts DISTINCT EVENTS rather than markets. Comma list of
    # kalshi_bot.mmsell.regimes names; env-overridable if a new correlated regime is identified.
    mmsell_settlement_correlated_regimes: str = "Elections"
    mmsell_settlement_event_cap: int = 5         # max distinct events open on one CORRELATED date
    # Ride-along: run the mmsell PAPER book inside the weather/live cycle (throttled), so it
    # collects alongside the weather books without a disruptive mode switch or any real money.
    # On by default now that we're forward-testing the maker edge; set false to disable.
    mmsell_paper_enabled: bool = True
    mmsell_interval_minutes: float = 30.0        # how often the ride-along entry scan runs
    # Record one price tick per open-mmsell-position ticker each management cycle (off the
    # orderbook already fetched — no extra API). Builds the intraday tape the sports tickers
    # never had, feeding the offline exit-rule study (scripts/mmsell_exit_study.py): confirmed
    # catastrophic stop + volatility exit vs hold-to-settlement. Positions still hold to
    # settlement — this is pure DATA CAPTURE, not an exit; the study measures counterfactually.
    mmsell_tick_capture_enabled: bool = True
    # Candidate capture: one orderbook snapshot per IN-BAND mmsell candidate each entry cycle
    # (off the book the entry scan already fetched — no extra API), whether or not a position was
    # opened. mmsell_position_ticks only tapes markets already HELD, so the fill model still leans
    # on a live-*calibrated* estimate (it can't replay the resting-order fill of the wider
    # candidate universe). This builds the pre-entry price tape that makes a direct per-ticker
    # fill replay possible ("would a resting buy-NO at the no-bid have been lifted before close?")
    # — the step-0 collection for the next mmsell live re-test (docs/MMSELL_FILL_MODEL.md §5 #2).
    # Pure DATA CAPTURE; changes no trading decision. Cap bounds the per-cycle write volume.
    mmsell_capture_candidates: bool = True
    mmsell_candidate_capture_max: int = 400
    # Revision books (parallel paper variants next to the untouched `mmsell` control), from
    # the 2026-07-04 forward decomposition of 445 settled trades: the maker-sell-and-hold
    # edge lives in the CHEAP longshots (yes 5-10c +2.7c/ct 96%win, 10-20c +3.6c 91%) and is
    # NEGATIVE in the mid band (20-35c -1.5c 74%, 35-50c -4.4c 60%) — the OPPOSITE of the raw
    # tape backtest, because hold-to-settlement harvests the favorite-longshot bias (cheap
    # longshots are the most overpriced), while mid-price contracts don't overprice enough to
    # cover their bigger loss-when-hit. The control's 5-40c band drags in the losing 20-40c
    # cells. Variants narrow to where the forward edge actually is. Spec format:
    #   "tag:key=val,...;tag:..."  keys: lo, hi (midpoint band cents), htcmin, htcmax (hours),
    #   maxyes (entry-price ceiling: cap the ACTUAL yes sell price = 100 - no-bid, cents),
    #   skip (series-substring blocklist), only (series-substring allowlist). skip/only take
    #   '+'-joined substrings matched (case-insensitive) against the series prefix — e.g.
    #   skip=WC+ATP means "drop any series containing WC or ATP"; only=TOTAL+SPREAD means "trade
    #   ONLY series containing TOTAL or SPREAD". Unset keys inherit the base mmsell_* knobs.
    #   Empty string disables all variants.
    # mmsell3 added 2026-07-10 from the per-yes-price-band history (n>1,700 settled): the
    # maker-sell edge is sharply concentrated in the CHEAPEST longshots — yes 5-10c nets
    # +2.2c/trade at 96% win over n=378 (highest volume band too), 10-15c is only +0.7c, and
    # 15-20c is NEGATIVE (-2.2c). mmsell1 (5-20) / mmsell2 (10-20) both dilute the 5-10c
    # winner with the flat/negative 10-20c cells. mmsell3 isolates the pure sweet spot.
    # mmsell4-8 added 2026-07-15 from the live+paper by-sport/by-market-type decomposition
    # (docs/MMSELL_VARIANTS_THESIS.md): mmsell3's pooled ~breakeven live P&L is a strongly +EV
    # non-WC book (+5.6c/ct, 96% win) canceled by a strongly -EV World Cup soccer book (81.7% win,
    # -9.9c/ct — structural in paper AND worse live from in-play adverse selection). Cricket and
    # tennis/cricket MATCH-winners are the other -EV cohorts; totals/spreads/props are the strongest.
    #   mmsell4 = clean book (5-10c minus WC + cricket + tennis);
    #   mmsell5 = totals/spreads/props only (market-type concentration);
    #   mmsell6 = ultra-cheap 5-8c (FLB-monotonicity-at-the-floor test);
    #   mmsell7 = short-dated (<=24h to close) variance test;
    #   mmsell8 = scheduled-settle only (crypto daily + event props) — the adverse-selection isolator.
    # mmsell9-11 added 2026-07-18 from the live 2x2 (price x type) decomposition at n=232: the +EV
    # concentrates in CHEAP (yes <=7c) x NON-winner markets (+2.3c, 96% win), while head-to-head
    # game/match winners (MLB game, tennis, cricket, esports — WC's structural successors) at 8-11c
    # net -6.2c. Both levers stack (each ~-3 to -4c). mmsell5 (totals/spreads/props) is the standout
    # of the first cohort, so these push further:
    #   mmsell9  = the sweet-spot cell: totals/spreads/props/crypto AND yes<=7c (both levers combined);
    #   mmsell10 = entry-price ceiling ONLY (yes<=7c, all types) — isolates the price lever, and is the
    #              candidate mechanism to promote into the LIVE mmsell3 entry if it beats the control;
    #   mmsell11 = no-late-entry (htcmin=6) — skip the in-play window (adverse-selection lever).
    mmsell_variants: str = (
        "mmsell1:lo=5,hi=20;mmsell2:lo=10,hi=20;mmsell3:lo=5,hi=10;"
        "mmsell4:lo=5,hi=10,skip=WC+ATP+ITF+WTA+T20+ODI;"
        "mmsell5:lo=5,hi=12,only=TOTAL+SPREAD+ASG+HRDERBY;"
        "mmsell6:lo=5,hi=8;"
        "mmsell7:lo=5,hi=10,htcmax=24;"
        "mmsell8:lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY;"
        "mmsell9:lo=5,hi=12,only=TOTAL+SPREAD+ASG+HRDERBY+BTCD+ETH,maxyes=7;"
        "mmsell10:lo=5,hi=10,maxyes=7;"
        "mmsell11:lo=5,hi=10,htcmin=6;"
        # --- ANCHOR SET (2026-07-30, docs/MMSELL_ANCHOR_SET.md) -------------------------
        # Every anchor book uses the mmsell10 base (lo=5,hi=10,maxyes=7) — the only
        # REALIZABLE EDGE config — so ENTRY is held constant and each book varies exactly one
        # MECHANIC. That makes mmsell10 itself the control: any anchor book's difference from
        # it is attributable to its mechanic alone.
        #   A1/A2/A3 = executing confirmed stop at yes-BID >= 12 / 20 / 30c for 2 cycles. The
        #     tight-vs-loose question the crypto backtest answered only IN-SAMPLE over a grid
        #     (docs/MMSELL_CRYPTO_STUDY.md); these are the pre-registered forward test, so the
        #     level is fixed per book instead of chosen after the fact.
        #   A4 = volatility ENTRY gate: skip when the pre-entry candidate tape has already moved
        #     >=6c over the last 6 ticks (backtest: calm entries +2.85/+5.25c at 100% win,
        #     active -39c). Fires only when history exists, so A4-vs-mmsell10 stays a clean A/B.
        #   A5 = short strangle: sell BOTH mutually-exclusive tails of one event (cheap YES on a
        #     high strike + cheap NO on a low strike), entered only when the event actually has
        #     both — that pairing IS the low-vol selection the backtest's +3.30c/pair came from.
        "mmsellA1:lo=5,hi=10,maxyes=7,stopl=12,stopk=2;"
        "mmsellA2:lo=5,hi=10,maxyes=7,stopl=20,stopk=2;"
        "mmsellA3:lo=5,hi=10,maxyes=7,stopl=30,stopk=2;"
        "mmsellA4:lo=5,hi=10,maxyes=7,volw=6,volv=6;"
        "mmsellA5:lo=5,hi=10,maxyes=7,strangle=1;"
        # Queue-position A/B as TWO live books (docs/MMSELL_OFFSET_AB.md). Same mmsell10 entry;
        # the only difference between them is where they rest. `abarm` splits the candidate flow
        # by a hash of the ticker, so neither book ever contests a ticker with the other and the
        # split is random rather than decided by book order. 1-contract clips (`size=1`) keep the
        # experiment's added live footprint small next to the incumbent mmsell10, which is
        # untouched and still takes its candidates first.
        #   mmsell10a = arm 0 -> rests AT the no-bid (the incumbent's behaviour = control)
        #   mmsell10b = arm 1 -> rests 1c BETTER, buying queue priority for 1c of premium
        # Both are INERT until MMSELL_LIVE_OFFSET_AB_ARMS is set AND the tag is in
        # LIVE_STRATEGIES: with no arms configured an arm book admits no tickers at all.
        "mmsell10a:lo=5,hi=10,maxyes=7,abarm=0,size=1;"
        "mmsell10b:lo=5,hi=10,maxyes=7,abarm=1,size=1"
    )
    # --- mmsell LIVE entry (maker NO-buy; inert until LIVE_STRATEGIES lists a mmsell tag) ---
    # The mmsell books rest a BUY-NO limit at the no-bid (== sell yes at the ask) and HOLD to
    # settlement — a MAKER order, unlike the weather books' YES-taker entries. The whole point of
    # the live test is to measure real fill rate + adverse selection (paper ASSUMES the resting
    # no-bid fills). Gated by the same three switches (BOT_MODE=live + KILL_SWITCH=false +
    # LIVE_ENABLED=true) AND a mmsell tag in LIVE_STRATEGIES; these knobs tune the maker entry.
    # See docs/MMSELL_LIVE_PLAN.md. All default to the safe Stage-1 (~$150, 1-contract) config.
    mmsell_live_max_open_positions: int = 60     # cap concurrent live mmsell positions (paper peak ~68)
    mmsell_live_price_offset_cents: int = 0      # bid this many cents ABOVE the no-bid: 0 = join the
    #                                              queue at the no-bid (Stage 1); 1 = improve to fill
    #                                              faster. Capped at the no-ask so it never pays through.
    mmsell_live_max_spread_cents: int = 40       # sanity guard only: skip if the yes spread exceeds
    #                                              this. Cheap longshots are wide by nature — the maker
    #                                              edge IS the spread — so this is generous, NOT the
    #                                              weather risk gate's 5c (which would reject the book).

    # --- mmsell LIVE "hot market" defensive pricing ---
    # A scheduled-event series (e.g. KXFEDMENTION, which reprices sharply as a Fed speech is
    # transcribed/scored live) can move fast enough that a normal resting price gets crossed and
    # Kalshi's post-only enforcement cancels it — confirmed live 2026-07-30: KXFEDMENTION-26JUL-PROJ's
    # no-bid moved 73c -> 94c in 32 minutes, and the order rested into that same move got canceled
    # with zero fill. This does NOT exclude any series — it still enters every candidate exactly as
    # before; a "hot" entry just prices more defensively (see maker_no_price in live/sizing.py).
    #
    # "Hot" = the ticker's current no-bid differs from the last candidate tick captured for it
    # (mmsell_candidate_ticks, already recorded every cycle for every in-band candidate, live or
    # not) by at least this many cents...
    mmsell_live_hot_market_move_cents: int = 5
    # ...within this many minutes back. No qualifying tick at all (the ticker was out of the
    # trading band for the whole lookback — itself what happened in the KXFEDMENTION case) also
    # counts as hot, since an absence right when the market is being entered is not evidence of calm.
    mmsell_live_hot_market_lookback_minutes: int = 30
    # On a hot entry, price at the no-bid PLUS this offset instead of the normal
    # mmsell_live_price_offset_cents. Negative (the default) rests BELOW the no-bid — extra
    # headroom against continued momentum in the same direction — rather than joining/improving
    # into the spread the way a calm entry does.
    mmsell_live_hot_market_defensive_offset_cents: int = -3

    # --- mmsell LIVE queue-position A/B (docs/MMSELL_OFFSET_AB.md; INERT by default) ---
    # mmsell_live_price_offset_cents has always been 0 (join the no-bid) and has never been
    # varied, so there is no data on what queue position is worth. It is the only untested live
    # knob that acts on maker adverse selection — the ~2c/contract gap that decided the mmsell3
    # live test (docs/MMSELL_FILL_MODEL.md) — and the retry data already showed the tickers live
    # MISSED earned the same in paper as the ones it captured (6.15 vs 6.26 c/contract), i.e.
    # lost volume rather than dodged bullets, which is the argument for paying to fill more.
    #
    # Rather than two live books (which would compete for the same tickers, double the exposure
    # and cross each other), this randomizes WITHIN one book: each ticker is assigned to an arm
    # by a deterministic hash, so total footprint is unchanged and the arms see the same market
    # flow. Comma-separated offsets in cents, e.g. "0,1" = half the tickers join the no-bid, half
    # bid 1c better. EMPTY (the default) disables the experiment entirely and restores exactly
    # today's single-offset behaviour. Hot entries are excluded from the split (they are priced
    # by the momentum guard) — see live/sizing.py maker_offset.
    mmsell_live_offset_ab_arms: str = ""
    # Changing the salt RE-RANDOMIZES every ticker's arm, which silently invalidates comparison
    # with anything collected under the old salt (the analysis recomputes assignment from it).
    # Bump it only to start a genuinely new experiment, and record the change in the doc.
    mmsell_live_offset_ab_salt: str = "mmsell-offset-ab-v1"

    # --- mmsell LIVE entry retry (recover the one-shot-per-ticker execution gap) ---
    # Paper never misses a fill, so its position stays open to settlement and the entry loop's
    # skip_already_open guard fires every later cycle — which ALSO skipped the live mirror, giving
    # live exactly one attempt per ticker for the ticker's whole life. Measured live 2026-07-31:
    # all 71 tickers in the epoch had exactly 1 live order, 29 of them never filled, and the missed
    # set earned the same as the captured one in paper (6.15 vs 6.26 c/contract) — i.e. real money
    # left on the table, NOT adverse selection being avoided. See mmsell/tracker.py's
    # _maybe_retry_live. Paper books are untouched by this; only the live mirror re-fires.
    #
    # Total live BUY attempts allowed per (ticker, book), counting cancelled ones. 1 restores the
    # old one-shot behaviour; 0 disables the retry path entirely.
    mmsell_live_max_attempts_per_ticker: int = 6
    # Retry only while the current no-bid is still within this many cents of the FIRST attempt's
    # limit price, so a retry never chases a market that has repriced away from the edge we
    # originally sized. The measured recoverable set sat inside 2c (13 of the 15 unfilled tickers
    # that were still in-band afterwards). The band/maxyes checks upstream already apply too.
    mmsell_live_retry_max_drift_cents: int = 2

    # --- mmsell LIVE closeout (one-shot, END-OF-STRATEGY only; inert by default) ---
    # mmsell was built hold-to-settlement only (the exit study proved TP/SL hurts) — there was
    # NEVER a path to exit a position early. This is that path, added 2026-07-19 to wind down
    # the mmsell3 live test: closes every open NO position for the listed strategies by BUYING
    # YES at the current ask (marketable IOC via the same V2 events endpoint as entries — crosses
    # the spread deliberately, since a close must guarantee execution, not rest as a maker).
    # Runs from LiveExecutor.close_mmsell_positions regardless of LIVE_STRATEGIES (so clearing
    # the entry allowlist stops new entries while this still closes what's open), but the
    # underlying order placement is STILL gated by the client's own bot_mode+KILL_SWITCH guard —
    # so KILL_SWITCH must be FALSE for the close orders to actually reach Kalshi. See the
    # shutdown sequence in docs/MMSELL_LIVE_PLAN.md. Self-limiting: once a strategy's positions
    # are flat, later cycles find nothing to close — no need to flip this back off.
    mmsell_closeout_enabled: bool = False
    mmsell_closeout_strategies: str = ""   # comma list of strategy prefixes, e.g. "mmsell3"
    mmsell_closeout_slippage_cents: int = 3  # cross up to yes-ask + this many cents to guarantee the fill
    # Give up on a ticker after this many close attempts. "Self-limiting" above holds only when
    # the closes actually FILL; a position that can't be closed is re-derived from Kalshi's
    # snapshot every cycle and retried forever. The mmsell3 wind-down (2026-07-19) left this flag
    # on with KILL_SWITCH=true and burned 1,942 dead live_orders rows over 8 tickers, 650 on the
    # worst one. Past the cap the executor logs once and leaves the position to a human. 0 =
    # unbounded (the old behaviour).
    mmsell_closeout_max_attempts_per_ticker: int = 5

    # --- mmsell REGIME settled-history capture (kalshi_bot/mmsell/history.py) ---
    # Kalshi serves only a rolling ~70-DAY window of settled markets (measured 2026-08-03: paging
    # to cursor exhaustion bottoms out on the same date for every series, KXNFLGAME returns zero,
    # finalized/closed return nothing, min_close_ts cannot reach behind it, and auth does not
    # help). So last season is unobtainable and the seasonal regime backtest is boxed into
    # whatever is inside the window on the day it runs. This capture writes history FORWARD so
    # that by October we own the NFL season the API will already have discarded.
    # See docs/MMSELL_SEASONAL_FORECAST.md.
    mmsell_history_enabled: bool = True
    # How often to re-enumerate. The window slides, so unlike the weather backfill this can never
    # latch "done"; 6h is far inside the ~70-day wall while costing ~1 pass per few hundred cycles.
    mmsell_history_enumerate_minutes: float = 360.0
    mmsell_history_markets_per_cycle: int = 30    # candle fetches per cycle (API budget guard)
    mmsell_history_capture_hours: float = 336.0   # 14d — mmsell's whole holding window (htcmax)
    mmsell_history_period_minutes: int = 60       # candle granularity: 1, 60 or 1440
    mmsell_history_min_volume: float = 100.0      # matches mmsell_min_volume: skip markets the
    #                                               book would never have entered anyway
    mmsell_history_max_markets_per_series: int = 3000  # per-series enumeration cap per pass
    # Series to capture, comma-separated. Deliberately EXPLICIT rather than prefix-discovered:
    # Kalshi lists 3,000+ sports series, most of them per-team spin-offs of one driver, and
    # enumerating them all would spend the whole API budget on noise. The regimes we have zero
    # paper history for come first — they are the ones we cannot reconstruct later.
    # To find new tickers before adding them here (the obvious guesses KXSENATE / KXHOUSE /
    # KXGOV / KXMIDTERM do NOT exist), run:
    #   {"type":"script","name":"mmsell_supply_forecast","args":["--list-series","NFL,Elections"]}
    # Env-overridable, so a series can be added on Railway without a redeploy.
    mmsell_history_series: str = (
        # NFL + college football — the September arrival, and completely unmeasurable today
        "KXNFLGAME,KXNFLTOTAL,KXNFLSPREAD,KXNCAAFGAME,KXNCAAFTOTAL,KXNCAAFSPREAD,"
        # NBA / NHL — October openings; our only sample today is 3 weeks of playoffs
        "KXNBAGAME,KXNBATOTAL,KXNBASPREAD,KXNBAPTS,"
        "KXNHLGAME,KXNHLTOTAL,KXNHLSPREAD,"
        # college basketball — November onward
        "KXNCAABGAME,KXNCAABTOTAL,KXNCAABSPREAD,"
        # MLB — the control: we DO have paper history here, so it validates the captured data
        "KXMLBGAME,KXMLBTOTAL,KXMLBSPREAD,KXMLBHR,"
        # Elections — the Nov-3 concentration question the forecast could not answer (0 entries
        # on 10 candled markets so far, which is exactly why the real ladders must be captured)
        "KXHOUSERACE,KXSENATEMID,KXGOVWINS,KXHOUSEWINSTATE,KXPRESPARTY"
    )

    # --- Theta book (ride-along paper, weather/live cycle) ---
    # Model-anchored tail-selling on the recurring hourly crypto ladders (docs/
    # THETA_THESIS.md). Validated 2026-07-03 (scripts/kalshi_theta_study.py): selling
    # every tail at the quote is ~0 EV, but selling only MODEL-overpriced 3-40c tails at
    # the ask inside the final hour netted +4.4c/contract net of worst-case fees, and the
    # realized maker-sell tape on these series is +5.2c with the edge <60min to expiry.
    # Entry = the mmsell maker convention (sell yes at ask == buy NO at no-bid), hold the
    # <1h to settlement. Paper-only; positions are settled by the shared paper engine.
    theta_enabled: bool = True
    # SHELVED 2026-07-09 (runs #21-#29): the family failed its pre-registered "positive AND
    # calibrated at n>=60" gate on every book. Live-confirmed by a full round-trip — the
    # control peaked at +$15.53 (n=495) then gave it all back to -$0.07 (n=542) within two
    # windows, exactly the miscalibrated-tail pattern (realized tails 1.4-2.6x the model).
    # collect_only keeps the tracker's crypto_ladder_snapshots + crypto_spot_candles
    # collectors alive (the labeled dataset a future fatter-tail model rebuilds from) while
    # skipping ALL entries (control + variants). theta_enabled stays True so the collector
    # still runs; set theta_collect_only=False to resume trading (requires a fresh
    # pre-registration per docs/THETA_THESIS.md).
    theta_collect_only: bool = True
    theta_interval_minutes: float = 5.0       # ride-along cadence (also snapshot cadence)
    # SERIES:COINBASE_PRODUCT pairs; wrong series fail soft (logged, skipped).
    theta_series: str = "KXBTCD:BTC-USD,KXBTC:BTC-USD,KXETHD:ETH-USD,KXETH:ETH-USD"
    theta_trail_days: float = 5.0             # spot window behind the return distribution
    theta_entry_min_minutes: float = 10.0     # don't sell inside the last 10min (stale loop)
    theta_entry_max_minutes: float = 55.0     # the tape edge lives <60min to expiry
    theta_snapshot_max_minutes: float = 90.0  # snapshot ladders this close to settlement
    theta_snapshot_rows_cap: int = 240        # bound rows written per cycle
    theta_price_lo_cents: float = 3.0         # tail band (yes mid), validated 3-40c
    theta_price_hi_cents: float = 40.0
    theta_min_edge_cents: float = 5.0         # mid - 100*P_model must clear this
    theta_min_volume: float = 100.0           # skip untraded strikes
    theta_order_size: int = 5                 # >=5 amortizes the fee ceil (exit study)
    theta_max_open_positions: int = 60
    theta_max_per_event: int = 3              # cap correlated strikes per hourly event
    # Revision books (parallel paper variants next to the untouched `theta` control), from
    # the 2026-07-04 live diagnosis at n=40: the bleed concentrated in 20-40c RANGE buckets
    # (model under-prices center mass: 19% modeled vs 37% realized) and in the earliest
    # 40-55min entries (-11.6c/ct); 10-20c and later entries were positive. Spec format:
    #   "tag:key=val,key=val;tag:..."  keys: lo, hi (band cents), edge (min edge cents),
    #   mult (vol multiplier), ttemin, ttemax (entry window min), thronly (1 = skip
    #   'between' range buckets). Unset keys inherit the base theta_* knobs above.
    #   theta1 = band+window surgery; theta2 = theta1 + thresholds-only (isolates whether
    #   range buckets are structurally bad); theta3 = wide config rescued only by a much
    #   higher bar + mildly widened tails. Empty string disables all variants.
    #   theta4 (added 2026-07-10, PRE-REGISTERED revival test): the shelve post-mortem found
    #   the model underprices realized tails by ~1.4-2.6x (median ~1.85x). theta4 attacks that
    #   directly with mult=2.0 (fattens the model tails ~2x to match realized) and only sells
    #   tails STILL overpriced AFTER fattening, in the cheap 3-20c band / final 35min.
    #   Hypothesis: if theta's failure is a simple ~2x tail-scale error, theta4 is + AND
    #   calibrated. It trades despite theta_collect_only via theta_live_variants below.
    #   EDGE LOOSENED 10c -> 6c (2026-07-11, pre-registered decision): at edge=10 theta4 made
    #   ZERO trades in ~24h+ (nothing is 10c+ overpriced after a 2x fatten — itself weak
    #   evidence the base tail miss really was ~2x, but it gives no tradeable signal). Per the
    #   run #32/#33 loop pre-registration ("loosen edge 10->6c or conclude"), edge=6 gets a
    #   testable n; the n>=80 gate (KEEP only if per-trade > 0 AND realized-tail-hit <= 1.25x
    #   modeled) now applies to this edge=6 sample. If it STILL barely trades or trades
    #   negative/miscalibrated, the fat-tail revival is impractical and theta stays fully shelved.
    theta_variants: str = (
        "theta1:hi=20,ttemax=35;"
        "theta2:hi=20,ttemax=35,thronly=1;"
        "theta3:edge=12,mult=1.25;"
        "theta4:hi=20,ttemax=35,mult=2.0,edge=6"
    )
    # Variants that keep trading even while theta_collect_only shelves the rest of the family
    # (control + theta1/2/3). Comma list of variant tags; empty = fully shelved (collect-only).
    # Set to a single pre-registered revival test at a time — NOT a way to quietly re-enable
    # the family. Currently: theta4 (the fat-tail calibration test).
    theta_live_variants: str = "theta4"

    # --- theta LIVE entry (maker NO-buy; inert until LIVE_STRATEGIES lists a theta tag) ---
    # Same maker convention as mmsell (rest a BUY-NO at the no-bid == sell yes at the ask, hold
    # to settlement) and the same purpose: theta's paper gate assumes the resting order always
    # fills, and docs/THETA_FILL_MODEL.md's borrowed-mmsell3-calibration read already flags that
    # theta is exposed to the same adverse-selection gap that hit mmsell3 live. This live path
    # exists to replace that borrowed calibration with theta's own ground truth. See
    # docs/THETA_LIVE_PLAN.md.
    #
    # Deliberately its OWN knobs, not a reuse of the mmsell_live_*/live_max_order_dollars /
    # max_order_size globals: two live books can now run at once, and sharing a dollar/contract
    # cap would mean resizing one silently resizes the other. theta's own paper clip
    # (theta_order_size=5) was chosen to amortize the fee ceiling; a shared cap tuned for mmsell
    # would undercut that. kalshi_bot/live/sizing.py takes these as explicit arguments for
    # exactly this reason — see its module docstring.
    theta_live_max_order_dollars: float = 3.0    # per-order dollar cap -> qty = floor(cap / price)
    theta_live_max_contracts: int = 5            # hard cap, independent of MAX_ORDER_SIZE
    theta_live_max_open_positions: int = 15      # cap concurrent live theta positions (paper cap 60)
    theta_live_price_offset_cents: int = 0       # 0 = join the queue at the no-bid, faithful to paper
    theta_live_max_spread_cents: int = 40        # sanity guard only, matches mmsell's — the maker
    #                                              edge IS the spread on these cheap tails

    # --- theta LIVE "hot market" defensive pricing ---
    # Same mechanism and rationale as mmsell_live_hot_market_* (see that block), with theta's OWN
    # knobs per this book's no-shared-live-knobs rule. Extended to theta after a live order was
    # HARD-REJECTED by Kalshi on 2026-08-02 — `400 invalid_order, details: "post only cross"` —
    # i.e. the book moved through our resting price between quote and placement. mmsell saw the
    # same failure as a cross-CANCEL; theta gets it as an outright rejection because the order
    # never rests at all, so the market is lost for the rest of its (short) window.
    #
    # Defaults are theta-shaped, NOT copied from mmsell: theta only trades 10-55 minutes to expiry
    # (theta_entry_min/max_minutes) and snapshots its ladder every theta_interval_minutes (5), so
    # a 30-minute lookback would span most of the tradeable window and read as "stale" almost
    # always. 15 minutes gives ~3 ladder rows to compare against while still being recent.
    theta_live_hot_market_move_cents: int = 5
    theta_live_hot_market_lookback_minutes: int = 15
    theta_live_hot_market_defensive_offset_cents: int = -3

    # --- theta LIVE closeout (one-shot, END-OF-STRATEGY only; inert by default) ---
    # Mirrors mmsell_closeout_* exactly (see that block's comment for the full rationale): a
    # manual flatten-everything escape hatch for a hold-to-settlement book that otherwise has no
    # early-exit path. Runs from LiveExecutor.close_theta_positions regardless of LIVE_STRATEGIES.
    theta_closeout_enabled: bool = False
    theta_closeout_strategies: str = ""    # comma list of strategy prefixes, e.g. "theta4"
    theta_closeout_slippage_cents: int = 3  # cross up to yes-ask + this many cents to guarantee the fill
    theta_closeout_max_attempts_per_ticker: int = 5  # see mmsell_closeout_max_attempts_per_ticker

    # --- TFAV book (ride-along paper, weather/live cycle) ---
    # The MIRROR of theta on the same recurring hourly crypto ladders: theta SELLS the
    # model-OVERpriced tails, tfav BUYS the model-UNDERpriced FAVORITES. Entry is a TAKER
    # buy of YES at the ask (not the maker no-bid the sell books use) on 65-90c favorites
    # inside the final hour whose model probability beats the ask by >= min_edge; held to
    # settlement (the shared paper engine settles it, like theta). Forward-test of
    # scripts/kalshi_favbuy_study.py — still EXPLORATORY (its P2/P4 gates aren't validated
    # yet); running it as a paper book is exactly how we accumulate the settled-trade data
    # that proves or kills it. tfav rides its own scan (shares theta's persisted spot window
    # in crypto_spot_candles, so the spot fetch is near-free once theta has run this cycle).
    # KILLED 2026-07-09 (runs #26-#29): crossed its pre-registered n>=150 gate NEGATIVE
    # (-3.6c/trade at n=210) after three straight window whipsaws — variance around a
    # negative mean, not an edge. The favorite-buy side of the favorite-longshot bias does
    # not net positive here. Disabled; crypto_spot/ladder collectors are unaffected (theta
    # owns those). Set back to True only under a fresh pre-registration.
    tfav_enabled: bool = False
    tfav_interval_minutes: float = 5.0        # ride-along cadence (matches theta)
    tfav_entry_min_minutes: float = 0.0       # final-hour edge window (minutes to close)
    tfav_entry_max_minutes: float = 60.0
    tfav_price_lo_cents: float = 65.0         # favorite band on the yes ASK (taker price)
    tfav_price_hi_cents: float = 90.0
    tfav_min_edge_cents: float = 5.0          # 100*p_model - yes_ask must clear this
    tfav_min_volume: float = 100.0            # skip untraded strikes
    tfav_order_size: int = 5                  # >=5 amortizes the fee ceil (like theta)
    tfav_max_open_positions: int = 60
    tfav_max_per_event: int = 3               # cap correlated strikes per hourly event
    # Revision books (parallel paper variants next to the untouched `tfav` control). Spec
    # format: "tag:key=val,key=val;tag:..."  keys: lo, hi (yes-ask band cents), edge (min
    # edge cents), ttemin, ttemax (entry-window minutes). Unset keys inherit the base
    # tfav_* knobs. Tag must start with 'tfav', differ from the control, fit String(24).
    tfav_variants: str = ""

    # --- PIN15 book (ride-along paper, weather/live cycle) ---
    # Endgame settlement-average observation-pin on Kalshi's 15-minute crypto up/down markets
    # (KXBTC15M/KXETH15M). Validated 2026-07-11 (scripts/kalshi_pin15_study.py, docs/PIN15_THESIS.md):
    # these settle on the 60-SECOND AVERAGE of the CF index over the final minute, so 2-3 min before
    # close a >= ~5bp spot displacement from the target already pins the outcome (settles the drift
    # way 96-100%, HOLDING ACROSS VOL QUARTILES — P4 pass), while the retail quote — anchored to the
    # flashing last-tick price — still prices the near-certain favorite at ~93-95c. PIN15 is a TAKER
    # buy of the drift-favored side (YES if spot>target, NO if spot<target) inside the final few
    # minutes, held to settlement (the shared paper engine settles it, like theta/tfav). Nets
    # +3.6-3.9c/ct net of the real ask + fee at T-120/180s in-sample; P3's SPIKEFADE mechanism FAILED
    # (the edge is plain drift-favorite underpricing, not a last-second-spike fade). The paper book
    # forward-tests the two remaining risks: real fill depth at the ask, and whether the ~300s loop
    # lands in the T~120-180s window (T-at-entry is recorded in fill_assumption for the slice).
    # Correlation caution: it's a favorite-BUY (the family tfav died in), the difference being the
    # now-vol-validated live spot-pin selection.
    # RETIRED 2026-07-16: the pre-registered T-at-entry slice (n=405 settled) falsified the
    # thesis — the target T~120-180s window earns only +0.27c/trade (well under the +1.5c
    # keep-bar), and the book's entire cumulative loss traces to one sub-window (T 60-120s
    # entries at -53c/trade). See docs/RESEARCH_JOURNAL.md and docs/BOOK_REGISTRY.md.
    pin15_enabled: bool = False
    pin15_interval_minutes: float = 0.0        # EVERY cycle — must be frequent to catch the window
    # SERIES:COINBASE_PRODUCT pairs; wrong series fail soft (logged, skipped).
    pin15_series: str = "KXBTC15M:BTC-USD,KXETH15M:ETH-USD"
    pin15_entry_min_seconds: float = 45.0      # skip the thinnest/most-expensive final seconds
    pin15_entry_max_seconds: float = 210.0     # stay in the validated T-180..T-0 zone (best 120-180)
    pin15_min_disp_bps: float = 5.0            # |spot-target|/target in bps; the pin threshold
    pin15_min_volume: float = 50.0             # skip untraded windows
    pin15_order_size: int = 5
    pin15_max_open_positions: int = 20         # <=20 concurrent 15-min windows
    pin15_max_per_event: int = 1               # one entry per window

    # --- XGAME in-play tape collector (ride-along, weather/live cycle) ---
    # COLLECT ONLY, no trading: stores both venues' trade tapes for matched in-play game
    # markets (Kalshi per-team moneyline vs Polymarket same-team/day market) into
    # game_market_matches / game_tape_snapshots — the dataset behind the XGAME lead-lag
    # thesis (docs/IDEA_MODEL_20260704.md). Trades carry the venues' own timestamps, so
    # the ride-along poll cadence bounds LOSS RISK (very deep tapes truncating between
    # polls), not bar resolution — the analysis builds ~10s bars from the timestamps.
    xgame_enabled: bool = True
    xgame_interval_minutes: float = 3.0       # ride-along poll cadence
    xgame_discovery_minutes: float = 30.0     # how often to re-match markets across venues
    xgame_series: str = "KXWCGAME"            # comma list of Kalshi game series to match
    # PM tag slugs holding the per-game "Will <team> win on <date>?" markets that pair with
    # KXWCGAME. FIXED 2026-07-05: the old "soccer" default pulled CLUB soccer (145 club teams,
    # zero overlap with the World Cup national teams KXWCGAME lists) so the (day,team) join
    # matched 0 pairs; the WC per-game markets live under the fifa-world-cup tags. Verified live
    # (scripts/xgame_match_debug): these tags take the match count 0 -> 13. The tag MUST match
    # the sport/tournament of xgame_series (add e.g. "mlb" if KXMLBGAME is ever added).
    xgame_pm_tags: str = "fifa-world-cup,2026-fifa-world-cup"
    xgame_pm_pages: int = 6                   # Gamma discovery pages (100 events/page) per tag
    xgame_max_matches: int = 60               # cap on concurrently-active matched pairs
    xgame_kalshi_trade_pages: int = 6         # Kalshi tape pages (1000 trades) per poll
    xgame_pm_trade_pages: int = 8             # PM data-api pages (500 trades) per poll
    xgame_overlap_seconds: int = 180          # re-fetch overlap behind the high-water mark
    xgame_ended_grace_minutes: float = 120.0  # keep polling this long past market close
    # XGAME paper BOOK (rides ON TOP of the tape collector above). Forward-test of the
    # cross-venue lead-lag thesis (scripts/xgame_tape_study.py): Polymarket leads, Kalshi
    # lags. When PM's P(team) has jumped >= shock_cents and Kalshi has NOT yet followed
    # (the live gap |pm_now - kal_now| >= min_gap_cents in the same direction), the book
    # buys the lagging Kalshi side TAKER at the ask and rides the catch-up. UNLIKE every
    # other book it does NOT hold to settlement — its edge is a 20-90s convergence — so the
    # tracker manages its OWN exit: close at the current Kalshi bid once Kalshi has repriced
    # >= converge_frac of the gap, or after hold_seconds, whichever first. Still exploratory
    # (P1/P2/P3 unproven); paper accumulates the data. Off-by-default sub-knob aside, the
    # book is ON by default so it starts forward-testing immediately.
    # SHELVED 2026-07-09 (xgame_tape_study on 19 matched WC games): the lead-lag thesis
    # failed its pre-registered gate. P2 KILL (median net follow-through -2c after costs)
    # and P3 FAIL (PM->K 58% vs K->P 59% — SYMMETRIC, because both venues just track the
    # match itself; there is no venue that leads). The book had 0 trades. Disabled; the
    # xgame_enabled tape COLLECTOR is left running to finish out the tournament. The
    # lead-lag family is ruled out — re-enabling needs a genuinely new mechanism, not a knob.
    xgame_book_enabled: bool = False
    xgame_shock_cents: float = 3.0            # min recent PM jump to call it a shock
    xgame_shock_window_seconds: float = 60.0  # lookback for the PM jump + the Kalshi level
    xgame_min_gap_cents: float = 3.0          # min live pm_now - kal_now gap to enter
    xgame_converge_frac: float = 0.8          # exit once Kalshi closes this frac of the gap
    xgame_hold_seconds: float = 120.0         # hard exit cap (the follow-through horizon)
    xgame_order_size: int = 5
    xgame_book_max_open_positions: int = 30   # one live position per matched game (deduped)

    # --- WCPROP book (ride-along paper, weather/live cycle) ---
    # World Cup cross-market coherence forward-test (scripts/xmarket_wc.py): does the
    # tournament-WINNER ladder (KXMENWORLDCUP) lag a decisive MATCH result? When a
    # KXWCGAME/KXWCROUND match settles, the involved teams' winner contracts should still
    # be catching up. The book finds winner contracts that MOVED over the recent window
    # (the causal, no-lookahead direction signal — the offline probe used the realized
    # [T,T+H] move, which a live book can't see) and enters TAKER in the move direction on
    # liquid, tight-spread rungs. NOT held to settlement (that would swap the lag residual
    # for a weeks-long tournament bet): the shared engine times it out at wcprop_hold_minutes,
    # closing at the current bid. Still exploratory (P1/P2/P3 unproven); paper accrues data.
    # KILLED 2026-07-09 (runs #21-#29): the book was armed and cycling every ~10min through
    # the ENTIRE knockout stage (15+ matched games settling) and opened ZERO trades — no
    # winner rung ever moved >= wcprop_min_move_cents within 45min of a match settling on a
    # liquid, non-rail-pinned rung. The post-match repricing either completes inside one
    # cycle or does not happen: no lag is harvestable at ride-along cadence. Winner ladder is
    # efficiently priced (the probe's P1 kill), confirmed forward. Disabled.
    wcprop_enabled: bool = False
    wcprop_interval_minutes: float = 10.0     # ride-along cadence (matches games are slow)
    wcprop_match_series: str = "KXWCGAME,KXWCROUND"   # settled match markets that trigger
    wcprop_winner_series: str = "KXMENWORLDCUP"       # the lagging winner ladder we trade
    wcprop_lookback_minutes: float = 45.0     # consider matches that closed within this
    wcprop_min_age_minutes: float = 5.0       # ...but not younger than this (the +5m entry)
    wcprop_min_move_cents: float = 3.0        # winner rung must have moved >= this recently
    wcprop_spread_cap_cents: float = 5.0      # skip wide/illiquid winner quotes
    wcprop_hold_minutes: float = 120.0        # timed exit horizon (the repricing window H)
    wcprop_order_size: int = 5
    wcprop_max_open_positions: int = 40

    weather_top_n: int = 10
    weather_entry_hours: str = "20,14,8"
    # Base books to run (favorite | nws | cal | "none"). PRUNED to "none" on 2026-07-04:
    # the per-book forward P&L showed `con` is the ONLY +EV weather book (+$9.83, +4.1c/trade
    # over 239) and every other book bleeds — fav -$49, nws -$64, cal -$72, dist -$27, cwin
    # -$7, obs -$6, pm -$6, favband -$4. So all base books are off and the flag-gated bleeders
    # (dist/cwin/favband/obs-book/pm-book) are disabled below; only `con` trades. Data
    # collectors that feed `con` (forecasts, ensembles, obs, Polymarket) stay ON. Set to a
    # comma list of the valid names to resume base books.
    weather_strategies: str = "none"
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
    # Pruned: the entire low-temperature program bled ~-$88 in paper with no +EV sub-book
    # (low_con was 11 trades / -$0.99), so lows are off by default. Set true to resume.
    weather_track_lows: bool = False
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
    # `weather_polymarket_enabled` gates the Polymarket DATA (bucket prices → stored +
    # fed to the `con` consensus as the `pm` family). `weather_pm_book_enabled` separately
    # gates the `weather_pm` BOOK that trades Kalshi toward Polymarket — PRUNED 2026-07-04
    # (-$6.15/158, -3.9c/trade) while data collection stays ON for con (mirrors the
    # obs data-vs-book split).
    weather_polymarket_enabled: bool = True
    weather_pm_book_enabled: bool = False
    weather_pm_cities: str = "LAX,MIA,AUS"
    weather_pm_interval_minutes: float = 5.0
    # Per-city entry-window book (`weather_cwin`): the backfill-validated optimal
    # hours-to-close for the HIGH favorite per city (h18 for CHI/LAX/DEN won an
    # out-of-sample holdout). Buys the favorite once at that city's window.
    weather_city_window_enabled: bool = False  # PRUNED 2026-07-04 (cwin -$6.81/130, -5.2c)
    weather_city_windows: str = "CHI:18,LAX:18,DEN:18,NYC:10,MIA:24,AUS:24,PHIL:10"
    # Per-city favorite PRICE-BAND book (`weather_favband`): buy the HIGH favorite at the
    # normal entry windows only when its implied price sits in a per-city band. The LAX
    # favorite is underpriced at 50-70c but OVERpriced >70c (over-paying for overshoot
    # risk) — buying only in-band survived an out-of-sample date split AND the h20/h14
    # windows in the backfill calibration study. Format: CITY:lo-hi cents.
    weather_favband_enabled: bool = False  # PRUNED 2026-07-04 (favband -$3.65/41, -8.9c)
    weather_favband_bands: str = "LAX:50-70"
    # Obs-confirmed late entry (`weather_obs` / `weather_low_obs`): after the local
    # cutoff hour the day's high/low has usually formed, so the station's running
    # max/min is a near-locked bound the market lags. Buy the bucket containing it
    # once, if its ask is still <= the cap (the entry study showed +EV late & cheap).
    weather_obs_entry_enabled: bool = False  # PRUNED 2026-07-04 (obs book -$6.35/171, -3.7c);
    #                                          weather_obs_enabled (data) stays ON for con
    weather_obs_high_after_hour: int = 16  # local hour; the high typically forms by ~3-4pm
    weather_obs_low_after_hour: int = 7    # the overnight low typically forms by ~dawn
    weather_obs_ask_cap: float = 90.0      # skip if the running-extreme bucket is already rich
    # Distribution edge book (`weather_dist` / `weather_low_dist`): price every bucket off
    # the stored Open-Meteo ensemble (a Gaussian kernel per member, blended across models),
    # and buy the single bucket whose model probability most beats its ask. sigma is the
    # kernel width (deg F) for forecast error beyond the ensemble spread — kept modest so
    # the model distribution stays tighter than the market's overdispersed ladder (#6).
    weather_dist_enabled: bool = False  # PRUNED 2026-07-04 (dist -$26.55/669, -4.0c); the
    #                                     ensemble DATA (weather_ensemble_enabled) stays ON for con
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
    # City-restricted consensus book (`weather_concity_h*`), added 2026-07-10 from the
    # all-time by-city con history (n=23-63/city): the con edge is concentrated by CITY, not
    # window. Winners AUS +11.7c/trade (n=63), CHI +5.8c (n=31), NY +5.7c (n=23); losers LAX
    # -11.6c, DEN -9.5c, PHIL -6.1c; MIA ~flat. weather_concity rides the SAME consensus pick
    # as `weather_con` but only enters for the allowlisted edge cities — a parallel A/B to
    # test whether restricting to the winners turns the (barely-negative) con book positive.
    weather_con_city_enabled: bool = True
    # con-city allowlist = City.code values (NB: New York's code is 'NYC', not the 'NY' series
    # suffix). Winners AUS/CHI/NYC; excluded losers LAX/DEN/PHIL and flat MIA.
    weather_con_allow_cities: str = "AUS,CHI,NYC"
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
    # Cancel an unfilled RESTING order after this long. Both extremes are known-bad, from live:
    #   * the old 600s (10min) cancelled maker orders long before a cheap tail could realistically
    #     be lifted — 16 of mmsell10's 29 misses in the 07-26 epoch were this timeout — and back
    #     then a cancel meant the ticker was lost forever (paper's open position blocked re-entry).
    #   * the 20-day value it was raised to went too far the other way: orders stopped being
    #     cancelled at all, so they were never re-priced. On 2026-08-02 the oldest resting mmsell
    #     order was 46.7h old, i.e. quoting a two-day-old book — a free option written to the
    #     market, which fills exactly when the market has moved against us.
    # 4h is the middle setting that composes with the entry retry (mmsell tracker's
    # _maybe_retry_live): the stale order is cancelled, and the next cycle re-posts at the CURRENT
    # price. Cancel-and-reprice is what a maker actually does; neither half works alone.
    # theta is unaffected either way — it only trades 10-55min to expiry, so its markets settle
    # long before 4h elapses.
    live_order_timeout_seconds: int = 14_400  # 4 hours
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

    # --- Live/paper PARALLEL TWIN books (docs/LIVE_PAPER_TWIN.md) ---
    # Standing policy: every strategy promoted to real money runs a FRESH paper book beside it,
    # started at the same instant and parameterized to the LIVE knobs (live entry price rule,
    # live dollar sizing, live open cap, live market-quality gates) — so the ONLY difference
    # between the two books is the fill assumption paper structurally cannot test. That converts
    # "is our paper edge a mirage?" from an argument into a measurement.
    #
    # Why not just compare live against the incumbent paper book: the incumbent carries months of
    # history and a different parameterization (1-contract clips, 200-position cap, paper's own
    # entry price), so any gap conflates sample, regime, sizing and fills. The twin controls all
    # of those; only fills differ.
    live_paper_twin_enabled: bool = True
    # Auto-twin every tag in LIVE_STRATEGIES (the standing policy). Explicit LIVE_PAPER_TWINS
    # entries always win; set this false to opt a deployment out entirely.
    live_paper_twin_auto: bool = True
    # Explicit pairs, comma-separated: "mmsell10" (twin tag derived by suffix) or
    # "mmsell10:mmsell10_pt" (explicit twin tag). Overrides/extends the auto list.
    live_paper_twins: str = ""
    live_paper_twin_suffix: str = "_pt"
    # 0 = inherit the live book's own open cap (the faithful choice — the twin should be
    # constrained exactly like live). Set >0 only to bound the extra paper bookkeeping.
    live_paper_twin_max_open_positions: int = 0
    # Record the per-candidate decision tape (parent paper / twin / live outcome per market per
    # cycle) that makes divergence attributable. Capped per cycle to bound row volume.
    live_paper_twin_parity_events: bool = True
    live_paper_twin_parity_max: int = 400

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
        raw = self.weather_strategies.strip().lower()
        if raw in ("none", "off"):  # explicit: run NO base fav/nws/cal book (con still runs)
            return []
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
    def live_paper_twin_pairs(self) -> list[tuple[str, str]]:
        """Configured (live_tag, twin_tag) parallel-run pairs.

        Sources, in precedence order: explicit `live_paper_twins` entries, then — when
        `live_paper_twin_auto` is on — every tag in `LIVE_STRATEGIES` that has no explicit
        entry. The twin tag defaults to `<live_tag><live_paper_twin_suffix>`, clamped to the
        24-char `paper_trades.strategy` width. A pair whose two tags collide, or whose twin tag
        is itself a live tag, is dropped (a twin must never be able to place real orders)."""
        if not self.live_paper_twin_enabled:
            return []
        live_tags = self.live_strategy_list
        suffix = (self.live_paper_twin_suffix or "_pt").strip()
        pairs: dict[str, str] = {}   # live_tag -> twin_tag
        for part in self.live_paper_twins.split(","):
            part = part.strip()
            if not part:
                continue
            live_tag, _, twin_tag = part.partition(":")
            live_tag = live_tag.strip()
            twin_tag = (twin_tag.strip() or f"{live_tag}{suffix}")[:24]
            if live_tag and twin_tag and live_tag != twin_tag:
                pairs[live_tag] = twin_tag
        if self.live_paper_twin_auto:
            for live_tag in live_tags:
                if live_tag not in pairs:
                    twin_tag = f"{live_tag}{suffix}"[:24]
                    if twin_tag != live_tag:
                        pairs[live_tag] = twin_tag
        # A twin tag that is also an allowlisted live tag would be able to place real orders.
        return [(lt, tt) for lt, tt in sorted(pairs.items()) if tt not in live_tags]

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

    @property
    def mmsell_settlement_correlated_regimes_list(self) -> set[str]:
        return {s.strip() for s in self.mmsell_settlement_correlated_regimes.split(",")
                if s.strip()}

    @property
    def mmsell_live_offset_ab_arm_list(self) -> tuple[int, ...]:
        """Parsed queue-position A/B arms (offsets in cents). Empty tuple = experiment off, which
        is both the default and the fail-safe: a malformed token yields no arms rather than a
        partial split, so a typo can never silently run half the book at an unintended price."""
        raw = [s.strip() for s in self.mmsell_live_offset_ab_arms.split(",") if s.strip()]
        if not raw:
            return ()
        try:
            arms = tuple(int(s) for s in raw)
        except ValueError:
            return ()
        # A single arm is not an experiment; treat it as off so the analysis never reports a
        # one-armed "A/B" that cannot answer anything.
        return arms if len(arms) >= 2 else ()

    @property
    def mmsell_history_series_list(self) -> list[str]:
        """Series the settled-history capture enumerates. De-duplicated and order-preserving, so
        an env override that repeats a ticker cannot double the enumeration cost."""
        out: list[str] = []
        for s in self.mmsell_history_series.split(","):
            tk = s.strip().upper()
            if tk and tk not in out:
                out.append(tk)
        return out

    @property
    def mmsell_variant_list(self) -> list[dict]:
        """Parsed revision-book specs (unset keys inherit the base mmsell knobs). The tag
        must start with 'mmsell', differ from the control's own 'mmsell', and fit the
        strategy column (String(24)). Malformed tokens / inverted bands are skipped."""
        out: list[dict] = []
        for spec in self.mmsell_variants.split(";"):
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            tag, _, body = spec.partition(":")
            tag = tag.strip()
            if not tag.startswith("mmsell") or tag == "mmsell" or len(tag) > 24:
                continue
            v = {
                "tag": tag,
                "lo": float(self.mmsell_entry_lo_cents),
                "hi": float(self.mmsell_entry_hi_cents),
                "htcmin": self.mmsell_min_hours_to_close,
                "htcmax": self.mmsell_max_hours_to_close,
                "skip": [],   # series-substring blocklist (case-insensitive; '+'-joined)
                "only": [],   # series-substring allowlist (empty = admit all)
                "maxyes": None,  # entry-price ceiling: cap the actual yes sell price (cents)
                # --- anchor-set mechanics (docs/MMSELL_ANCHOR_SET.md); None = disabled ---
                "stopl": None,   # EXECUTING catastrophic stop: exit when the yes-BID reaches this
                "stopk": 2,      # ...for this many CONSECUTIVE management cycles (confirm)
                "volw": None,    # vol ENTRY gate: look back this many candidate ticks
                "volv": None,    # ...skip the entry if their yes-mid range reaches this many cents
                "strangle": False,  # also sell the mirror (cheap-NO) tail, paired within an event
                # --- queue-position A/B (docs/MMSELL_OFFSET_AB.md); None = not in the experiment
                # `abarm` makes this book take ONLY the tickers whose deterministic hash arm equals
                # it, and price at mmsell_live_offset_ab_arms[abarm]. Two books declaring abarm=0
                # and abarm=1 therefore split the candidate flow between them at random, with no
                # ticker ever contested — which is what makes them a randomized A/B rather than a
                # race decided by book order.
                "abarm": None,
                # Per-book live contract cap, overriding the global max_order_size. Lets an
                # experiment run 1-contract clips beside an incumbent sized differently.
                "size": None,
            }
            ok = True
            for kv in body.split(","):
                kv = kv.strip()
                if not kv:
                    continue
                key, _, val = kv.partition("=")
                key = key.strip().lower()
                try:
                    if key in ("lo", "hi", "htcmin", "htcmax", "maxyes", "stopl", "volv"):
                        v[key] = float(val)
                    elif key in ("stopk", "volw", "abarm", "size"):
                        v[key] = int(val)
                    elif key == "strangle":
                        v[key] = str(val).strip() not in ("", "0", "false", "False")
                    elif key in ("skip", "only"):
                        # Series filter: '+'-joined substrings (can't use , ; : which the
                        # variant/spec grammar already claims). Matched against the series prefix.
                        v[key] = [t.strip().upper() for t in val.split("+") if t.strip()]
                    else:
                        ok = False
                except (TypeError, ValueError):
                    ok = False
            if ok and v["lo"] < v["hi"] and v["htcmin"] < v["htcmax"]:
                out.append(v)
        return out

    def mmsell_book_by_tag(self, tag: str | None) -> dict | None:
        """The parsed variant spec for one book tag, or None. Lets the shared paper engine look up
        a held position's own exit mechanic (e.g. the anchor set's executing stop) from just the
        `paper_trades.strategy` value, without the tracker having to hand it down."""
        if not tag:
            return None
        for v in self.mmsell_variant_list:
            if v["tag"] == tag:
                return v
        return None

    @property
    def mmsell_closeout_strategy_list(self) -> list[str]:
        return [s.strip() for s in self.mmsell_closeout_strategies.split(",") if s.strip()]

    @property
    def theta_series_map(self) -> dict[str, str]:
        """SERIES -> Coinbase product, parsed from "KXBTCD:BTC-USD,..."; skips malformed."""
        out: dict[str, str] = {}
        for tok in self.theta_series.split(","):
            series, _, product = tok.partition(":")
            if series.strip() and product.strip():
                out[series.strip().upper()] = product.strip().upper()
        return out

    @property
    def pin15_series_map(self) -> dict[str, str]:
        """SERIES -> Coinbase product, parsed from "KXBTC15M:BTC-USD,..."; skips malformed."""
        out: dict[str, str] = {}
        for tok in self.pin15_series.split(","):
            series, _, product = tok.partition(":")
            if series.strip() and product.strip():
                out[series.strip().upper()] = product.strip().upper()
        return out

    @property
    def theta_variant_list(self) -> list[dict]:
        """Parsed revision-book specs. Each dict carries the FULL resolved parameter set
        (unset keys inherit the base theta knobs); malformed tokens are skipped. The tag
        must start with 'theta', differ from the control's own 'theta', and fit the
        strategy column (String(24))."""
        out: list[dict] = []
        for spec in self.theta_variants.split(";"):
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            tag, _, body = spec.partition(":")
            tag = tag.strip()
            if not tag.startswith("theta") or tag == "theta" or len(tag) > 24:
                continue
            v = {
                "tag": tag,
                "lo": self.theta_price_lo_cents,
                "hi": self.theta_price_hi_cents,
                "edge": self.theta_min_edge_cents,
                "mult": 1.0,
                "ttemin": self.theta_entry_min_minutes,
                "ttemax": self.theta_entry_max_minutes,
                "thronly": False,
            }
            ok = True
            for kv in body.split(","):
                kv = kv.strip()
                if not kv:
                    continue
                key, _, val = kv.partition("=")
                key = key.strip().lower()
                try:
                    if key in ("lo", "hi", "edge", "mult", "ttemin", "ttemax"):
                        v[key] = float(val)
                    elif key == "thronly":
                        v[key] = val.strip() in ("1", "true", "yes")
                    else:
                        ok = False
                except (TypeError, ValueError):
                    ok = False
            if ok and v["lo"] < v["hi"] and v["ttemin"] < v["ttemax"]:
                out.append(v)
        return out

    @property
    def theta_live_variant_set(self) -> set[str]:
        """Variant tags that trade even while theta_collect_only shelves the rest of the
        family. Empty set => fully shelved (collect-only)."""
        return {t.strip() for t in self.theta_live_variants.split(",") if t.strip()}

    @property
    def theta_closeout_strategy_list(self) -> list[str]:
        return [s.strip() for s in self.theta_closeout_strategies.split(",") if s.strip()]

    @property
    def weather_con_allow_city_set(self) -> set[str]:
        """City.code allowlist for the weather_concity book (upper-cased)."""
        return {c.strip().upper() for c in self.weather_con_allow_cities.split(",") if c.strip()}

    @property
    def xgame_series_list(self) -> list[str]:
        return [s.strip().upper() for s in self.xgame_series.split(",") if s.strip()]

    @property
    def xgame_pm_tag_list(self) -> list[str]:
        return [t.strip().lower() for t in self.xgame_pm_tags.split(",") if t.strip()]

    @property
    def tfav_variant_list(self) -> list[dict]:
        """Parsed tfav revision-book specs (unset keys inherit the base tfav knobs). The tag
        must start with 'tfav', differ from the control's own 'tfav', and fit String(24).
        Malformed tokens / inverted bands or windows are skipped."""
        out: list[dict] = []
        for spec in self.tfav_variants.split(";"):
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            tag, _, body = spec.partition(":")
            tag = tag.strip()
            if not tag.startswith("tfav") or tag == "tfav" or len(tag) > 24:
                continue
            v = {
                "tag": tag,
                "lo": float(self.tfav_price_lo_cents),
                "hi": float(self.tfav_price_hi_cents),
                "edge": float(self.tfav_min_edge_cents),
                "ttemin": float(self.tfav_entry_min_minutes),
                "ttemax": float(self.tfav_entry_max_minutes),
            }
            ok = True
            for kv in body.split(","):
                kv = kv.strip()
                if not kv:
                    continue
                key, _, val = kv.partition("=")
                key = key.strip().lower()
                try:
                    if key in ("lo", "hi", "edge", "ttemin", "ttemax"):
                        v[key] = float(val)
                    else:
                        ok = False
                except (TypeError, ValueError):
                    ok = False
            if ok and v["lo"] < v["hi"] and v["ttemin"] < v["ttemax"]:
                out.append(v)
        return out

    @property
    def wcprop_match_series_list(self) -> list[str]:
        return [s.strip().upper() for s in self.wcprop_match_series.split(",") if s.strip()]

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
            "mmsell_tick_capture_enabled": self.mmsell_tick_capture_enabled,
            "mmsell_capture_candidates": self.mmsell_capture_candidates,
            "mmsell_candidate_capture_max": self.mmsell_candidate_capture_max,
            "mmsell_live_max_open_positions": self.mmsell_live_max_open_positions,
            "mmsell_live_price_offset_cents": self.mmsell_live_price_offset_cents,
            "mmsell_live_offset_ab_arms": list(self.mmsell_live_offset_ab_arm_list),
            "mmsell_live_offset_ab_salt": self.mmsell_live_offset_ab_salt,
            "mmsell_live_max_spread_cents": self.mmsell_live_max_spread_cents,
            "mmsell_live_hot_market_move_cents": self.mmsell_live_hot_market_move_cents,
            "mmsell_live_hot_market_lookback_minutes": self.mmsell_live_hot_market_lookback_minutes,
            "mmsell_live_hot_market_defensive_offset_cents":
                self.mmsell_live_hot_market_defensive_offset_cents,
            "mmsell_live_max_attempts_per_ticker": self.mmsell_live_max_attempts_per_ticker,
            "mmsell_live_retry_max_drift_cents": self.mmsell_live_retry_max_drift_cents,
            "mmsell_closeout_enabled": self.mmsell_closeout_enabled,
            "mmsell_closeout_strategies": self.mmsell_closeout_strategy_list,
            "mmsell_closeout_max_attempts_per_ticker":
                self.mmsell_closeout_max_attempts_per_ticker,
            "mmsell_variants": [f"{v['tag']}:{v['lo']:.0f}-{v['hi']:.0f}"
                                for v in self.mmsell_variant_list],
            "theta_enabled": self.theta_enabled,
            "theta_series": self.theta_series_map,
            "theta_entry_minutes": [self.theta_entry_min_minutes, self.theta_entry_max_minutes],
            "theta_band_cents": [self.theta_price_lo_cents, self.theta_price_hi_cents],
            "theta_min_edge_cents": self.theta_min_edge_cents,
            "theta_order_size": self.theta_order_size,
            "theta_max_open_positions": self.theta_max_open_positions,
            "theta_variants": [v["tag"] for v in self.theta_variant_list],
            "theta_live_variants": sorted(self.theta_live_variant_set),
            "theta_live_max_order_dollars": self.theta_live_max_order_dollars,
            "theta_live_max_contracts": self.theta_live_max_contracts,
            "theta_live_max_open_positions": self.theta_live_max_open_positions,
            "theta_live_price_offset_cents": self.theta_live_price_offset_cents,
            "theta_live_max_spread_cents": self.theta_live_max_spread_cents,
            "theta_live_hot_market_move_cents": self.theta_live_hot_market_move_cents,
            "theta_live_hot_market_lookback_minutes":
                self.theta_live_hot_market_lookback_minutes,
            "theta_live_hot_market_defensive_offset_cents":
                self.theta_live_hot_market_defensive_offset_cents,
            "theta_closeout_enabled": self.theta_closeout_enabled,
            "theta_closeout_strategies": self.theta_closeout_strategy_list,
            "theta_closeout_max_attempts_per_ticker":
                self.theta_closeout_max_attempts_per_ticker,
            "xgame_enabled": self.xgame_enabled,
            "xgame_series": self.xgame_series_list,
            "xgame_pm_tags": self.xgame_pm_tag_list,
            "xgame_interval_minutes": self.xgame_interval_minutes,
            "xgame_max_matches": self.xgame_max_matches,
            "xgame_book_enabled": self.xgame_book_enabled,
            "xgame_shock_cents": self.xgame_shock_cents,
            "xgame_min_gap_cents": self.xgame_min_gap_cents,
            "xgame_hold_seconds": self.xgame_hold_seconds,
            "tfav_enabled": self.tfav_enabled,
            "tfav_entry_minutes": [self.tfav_entry_min_minutes, self.tfav_entry_max_minutes],
            "tfav_band_cents": [self.tfav_price_lo_cents, self.tfav_price_hi_cents],
            "tfav_min_edge_cents": self.tfav_min_edge_cents,
            "tfav_order_size": self.tfav_order_size,
            "tfav_variants": [v["tag"] for v in self.tfav_variant_list],
            "wcprop_enabled": self.wcprop_enabled,
            "wcprop_match_series": self.wcprop_match_series_list,
            "wcprop_winner_series": self.wcprop_winner_series,
            "wcprop_hold_minutes": self.wcprop_hold_minutes,
            "wcprop_order_size": self.wcprop_order_size,
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
            "weather_obs_entry_enabled": self.weather_obs_entry_enabled,
            "weather_favband_enabled": self.weather_favband_enabled,
            "weather_city_window_enabled": self.weather_city_window_enabled,
            "weather_pm_book_enabled": self.weather_pm_book_enabled,
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
            "live_paper_twins": [f"{lt}->{tt}" for lt, tt in self.live_paper_twin_pairs],
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
