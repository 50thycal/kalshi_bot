# Weather-markets research journal

A running log of every edge hypothesis we've tested on Kalshi daily-temperature
markets, what the data said, and the verdict. Newest entries at the top. The
goal: a durable record of *why* each live book exists and why each dead idea was
abandoned, so we don't re-litigate settled questions.

Conventions:
- **EV/trade** is in cents per contract, **fees on both legs** (Kalshi
  `ceil(0.07·qty·P·(1−P)·100)`), unless noted.
- "backfill" = `backfill_weather_markets` / `backfill_weather_candles` (Kalshi
  REST history, hourly candles, spring Apr–Jun, last ~48h per market — separate
  provenance from the live-collected `weather_*` tables; never mixed silently).
- Probes live in `scripts/weather_backfill_edges.py` (structural edges) and
  `scripts/weather_window_sweep.py` (entry-window EV); run via the `ops` channel.

---

## IDEA-MODEL 2026-07-04 → Phase 2 built: TFAV / WCPROP / XGAME pipelines + pre-registered probes (verdicts pending)

*(2026-07-04. Pre-registered theses, predictions and decision rules in
`docs/IDEA_MODEL_20260704.md` — written BEFORE any validation ran; thresholds must not
be re-scoped post-hoc. This entry logs the Phase-2 data-pipeline/probe build only;
verdicts land here once the probes run on real samples.)*

The idea-model run screened 18 candidates → 3 promoted, and all three now have their
data pipelines + ops-runnable probes in place (run order = cost-to-verdict):

- **TFAV** (crypto hourly favorite-buy, the mirror of theta's parked side-finding):
  NO new collection needed — the theta collector already snapshots the FULL ladder
  (every price band ≤90min to settlement, model P attached), so 65-90¢ favorites are
  accumulating in `crypto_ladder_snapshots` since 2026-07-03. Probe
  `scripts/kalshi_favbuy_study.py` (ops: `kalshi_favbuy_study`) grades P1-P4:
  unconditional ~0 / model-filtered ≥+3¢ with split-half agreement / final-hour
  concentration / |corr| < 0.4 vs a theta-rule sim on the same events. Labels come
  from the Kalshi settlement archive (public REST), quotes+model from live snapshots.
- **WCPROP** (WC match-result → tournament-winner-ladder propagation lag): no
  collector needed — public 1-min candlesticks. Probe `scripts/xmarket_wc.py` (ops:
  `xmarket_wc`) detects result-known time from the match market's own pin (fallback
  close_time), measures winner-contract repricing completion at +1/+5/+15min and the
  net residual entering at the +5min quote (P1 <70% / P2 ≥+3¢ / P3 survives spread≤5¢).
  Runnable NOW against the live tournament.
- **XGAME** (in-play PM→Kalshi lead-lag on scoring shocks — the repo's standing #1
  frontier): the blocker was in-play tape collection, now built. New ride-along
  collector `kalshi_bot/xgame/` (COLLECT ONLY, no trading; `XGAME_*` config, default
  on) matches Kalshi per-team game markets (KXWCGAME) to Polymarket same-team/day
  markets by (day, normalized team) — precision over recall, ambiguous keys dropped,
  FULL clobTokenId stored — and polls BOTH venues' trade tapes (Kalshi
  `/markets/trades` with min_ts high-water marks; PM data-api, overlap+dedup) into
  `game_market_matches` / `game_tape_snapshots` (new tables, migration
  `f3a4b5c6d7e8`; separate provenance from all weather/crypto tables). Trades carry
  venue timestamps, so the probe builds ~10s bars regardless of poll cadence. Probe
  `scripts/xgame_tape_study.py` (ops: `xgame_tape_study`) grades P1-P4: follow%>55 &
  same-bar<40 / median net follow-through ≥4¢ / PM→K exceeds K→PM by ≥10pts / median
  exploitable window ≥20s.

Ops note: the three probes are allowlisted in `scripts/ops_runner.py`; refresh the
`ops` branch from the default branch after merge so the channel picks them up. The
XGAME collector starts filling tables on the next Railway deploy — watch the
`xgame collector` log line (kalshi_games/pm_games/matched tell you immediately if the
PM question-format match is off).

*(2026-07-04.)* First ~21h live: theta −$13/40 settled vs +4.4¢ backtest. Read-only
decomposition found the miss is CONCENTRATED, not uniform: **38/40 entries were KXBTC range
buckets at 20-40¢** (model 19% vs realized 37% — the trailing distribution under-weights
center mass, so "overpriced" flags on near-money buckets were model error) and **40-55min
entries carried the losses** (−11.6¢/ct vs positive at 10-40min — matching the tape's
edge-lives-late structure). 10-20¢ trades were +17.8¢/ct (tiny n). The unselected
snapshot-calibration set (spot-at-close labels) shows far tails perfectly calibrated and
3-40¢ *threshold* tails if anything over-priced by the model in this calm regime — so a
global tail-fattening fix is wrong. Shipped three pre-registered revision books next to the
untouched control (same scan/model, gates only): **theta1** (band 3-20¢ + tte 10-35m),
**theta2** (theta1 + thresholds-only), **theta3** (wide config + edge≥12¢ + mult 1.25).
Decision rule: ≥~60 settled/book, keep only positive-P&L books whose realized tail-hit ≤
modeled; all negative → shelve the family. Full detail in `docs/THETA_THESIS.md`.

## THETA book — hourly crypto ladders: naive tail-selling ~0, MODEL-FILTERED tail-selling +EV (built)

*(2026-07-03, Claude's own book — full thesis + pre-registered predictions in
`docs/THETA_THESIS.md`; probe `scripts/kalshi_theta_study.py`, ops-runnable.)*

Tested Kalshi's recurring hourly crypto ladders (KXBTCD/KXBTC/KXETHD/KXETH — 24
settles/day/series, deep 3-40c tails, retail lottery flow) on ~3-7 days of settled
history, 87.5k tape trades, and a Coinbase 1-min spot model (empirical remaining-window
return distribution, no lookahead):

- **Naive tail-selling at the quotes: ~0 EV** (T-30 gap +0.7c, n=494) — the posted
  quotes are calibrated on average. No unconditional band edge. (Same lesson as every
  price-only weather probe.)
- **The realized maker-SELL flow is +5.2c/contract net of worst-case fees** (2.09M
  contracts, split-half +5.11/+5.32), and by minutes-to-expiry the edge is entirely
  **inside the final hour**; >60min out it inverts negative. Maker-BUY mirror: -9.3c.
- **A spot-vol model separates dead tails from live ones at the same price**: selling
  only model-overpriced tails (mid - 100·P_model >= 5c) at the ask = **+4.44c/contract**
  (n=114); model-fair tails = -1.52c. Strongest: 10-20c overpriced -> +13.2c (win 2.6%
  vs 15.4 implied) — quote staleness after spot moves, the obs-lag family.
- Side-finding parked: hourly 65-90c favorites ran ~9-11c UNDERpriced (small n) — a
  possible future buy-the-favorite probe.

**Built the same day**: `theta` paper book (`kalshi_bot/theta/`) riding the weather/live
cycle like mmsell — collects 1-min spot (`crypto_spot_candles`) + near-settlement ladder
snapshots with the model probability attached (`crypto_ladder_snapshots`), and sells
model-overpriced 3-40c tails at the ask (buy NO at no-bid) ONLY at 10-55min to
settlement, qty 5, capped 3/event, hold-to-settlement. Caveats: the model split's n=114
covers one vol regime — the paper book is the real out-of-sample test (~100s of
trades/week); fills assume our ask is hit (mmsell's known limitation, partially derisked
because the tape measures realized passive fills).

## Standing verdict / themes

**Kalshi's temperature ladder is efficient on everything derivable from price
history.** Overround, persistence/autocorrelation, calibration across the price
range — all priced. The only positive-EV we've found comes from *information the
market is briefly slow to incorporate* (obs-confirmed late entry once the day's
real thermometer reading is in; cross-market signal from Polymarket) and from
*city-specific entry timing* (the city × window map). Everything that only uses
the ladder's own prices has come back dead or sub-fee.

Live books currently running: `fav` (control), `nws`, `cal`, `pm`
(Polymarket-cross), `cwin` (per-city high windows), `obs` (running-extreme late
entry). The legacy h12 window is flagged in the DB and excluded from the PnL
report.

### The bucket-probability edge model — NOW LIVE as the `dist` book
Built and shipped (`weather_dist` / `weather_low_dist`). How it works:
- **Engine:** `kalshi_bot/weather/distribution.py` — `member_bucket_prob` (Gaussian
  kernel, sigma = forecast error beyond ensemble spread) → `model_bucket_probs`
  (blend GFS+ECMWF → per-bucket P) → `best_bucket_by_edge` (buy the single bucket
  whose model prob most beats its ask, net of fee, by ≥ `weather_dist_min_edge_cents`).
  The live counterpart of the offline grader in `scripts/weather_model_check.py`
  (math duplicated there so the ops script stays self-contained).
- **Inputs:** reads the latest stored ensemble for (city, date, kind) from
  `weather_ensembles` (collected live by `OpenMeteoEnsembleClient`); self-gates —
  no ensemble → no trade. Enters per entry-window like fav/nws/cal/pm.
- **#6 baked in:** `weather_dist_sigma` default 1.5 °F keeps the model distribution
  tighter than the market's overdispersed ladder — the edge is precisely that the
  ensemble-grounded distribution is sharper than the crowd's.
- **Config:** `weather_dist_enabled` (true), `weather_dist_sigma` (1.5),
  `weather_dist_min_edge_cents` (5). Watch its settled P&L vs `fav` in the PnL table.
- **Still crude (kept as controls):** `nws`/`cal` buy only the single point-forecast
  bucket; `dist` is the real distribution model.

---

## First real-money live test ($1, Jun 14) — buy path CONFIRMED; 2 parser bugs fixed

Ran a tiny live round-trip via the env channel (relaxed spread + a fresh entry window to
force entries on near-close low favorites). Outcome:

- **The buy path works end-to-end on real money.** 3 BUY orders placed and FILLED on Kalshi
  (DEN low @82¢, CHI @99¢, LAX @88¢, 1 contract each), with real `kalshi_order_id`s captured
  and reconciled to `filled`. The env-channel control, place_order, fill reconciliation, and
  the risk gate (it correctly blocked `SPREAD_TOO_WIDE` until relaxed) all validated live.
- **Two reconciliation parser bugs — only real API data could reveal them — found & fixed:**
  1. **Fills**: Kalshi sends `yes_price_dollars`/`no_price_dollars` (dollar strings),
     `count_fp` (fixed-point), `fee_cost` (dollars) — not `yes_price`/`count`/`fee`. Fixed
     (reuse `price_to_cents`/`_to_count`). Confirmed: DEN @82¢/qty1/$0.0104 fee.
  2. **Positions**: fields are `position_fp` (signed FP), `market_exposure_dollars`,
     `realized_pnl_dollars` — not `position`/`market_exposure`/`realized_pnl`. The old parser
     left `realized_pnl` null, **silently disabling the daily-loss circuit breaker**. Fixed.
- **Known remaining issue — SELL/exit orders rejected** (`invalid_parameters`, 400). The
  exit-order param format is wrong for Kalshi. NOT needed for the validated books (they're
  hold-to-settlement), but must be fixed before using live TP/SL/break-even exits. The sl=1
  in the test was only a contrivance to force a round trip.
- **Lesson:** the demo/shape probe caught the read shapes (orders/balance) but the fill/
  position element fields could only be confirmed with a real fill. Always do a tiny live
  buy-and-reconcile before trusting the parsers.

Aftermath: kill switch back ON, config restored (spread 5, windows 20/14/8, exit settlement);
3 tiny low-favorite positions (~$2.69) left to settle (also validates the settlement path).

### Follow-up fixes (both confirmed)
- **Settlement path CONFIRMED on real money.** Added `/portfolio/settlements` reconciliation
  (settled positions vanish from get_positions, so realized losses could escape the daily-loss
  breaker). Verified against the test settlements: CHI B64.5 **+$0.005** (bought 99¢, won),
  DEN B54.5 **−$0.8304** (bought 82¢, lost = 0 − 0.82 − 0.0104 fee) — both computed correctly
  and now feed `live_realized_pnl_today`. Settlements shape (revenue cents, *_total_cost_dollars,
  fee_cost, market_result) confirmed via the probe before parsing.
- **SELL/exit bug FIXED.** A raw `action="sell"` yes order returns Kalshi `invalid_parameters`.
  Exits now close a YES position by **BUYing the opposite (NO) side** at `100 − yes_bid` —
  reusing the proven buy path; Kalshi nets the opposing position (same P&L as selling at the
  bid). Unit-tested; verified-by-construction (identical mechanics to the working entry buy).
- Net realized cost of the whole live test ≈ **−$0.83** (CHI +0.005, DEN −0.83; LAX pending).
  Worker restored to clean baseline: BOT_MODE=weather, KILL_SWITCH=true, live fully disarmed.

### Round-trip test #2 (buy → ~1min hold → close) — mechanics CONFIRMED; exit edge case
Sped cycles to 60s, forced fresh h18 entries, sl=1 to close. Result:
- **Full round trip works end-to-end:** DEN-T69 entry **buy YES @85¢ (filled)** → ~15s hold →
  exit **buy-NO @17¢ (FILLED)**, closing the position. The buy-NO close (the fix) executes on
  real money. ✓
- **Exit reliability edge case:** 2 of 3 exits (CHI high B69.5, CHI low B58.5) were rejected
  `400 invalid_parameters` on payloads STRUCTURALLY IDENTICAL to DEN's (differ only by ticker +
  no_price) — so it's not a code bug; likely a transient market-state condition for those
  buckets at that instant. A duplicate DEN exit got `409 order_already_exists` (Kalshi's
  client_order_id idempotency caught a fast-cycle retry race — a non-issue at the normal 300s
  cadence). **Takeaway: the buy-NO close is proven but not yet 100% reliable across markets;
  harden it (retry/fallback, confirm before relying on live TP/SL) before using non-settlement
  exits.** The validated books are hold-to-settlement (no exits), which is fully proven.
- Aftermath: 2 open CHI positions (@45, @71) left to settle; baseline restored, fully disarmed.

### Exit hardening (Phase 6) — robust close, not a new method
Research confirmed buy-NO-to-close is Kalshi's *canonical* close (it nets the pair → credits $1);
the CHI rejections were validation/market-state, not a wrong method. So the fix is robustness:
- **Position snapshot is the source of truth** (reconcile runs before manage_exits each cycle):
  an exit is "done" only when Kalshi shows the position flat; otherwise re-attempt.
- **Dedup bug fixed:** `live_exit_order_exists` counted `rejected` as committed, permanently
  blocking re-attempts. Split into `live_exit_in_flight` (non-terminal only) + `count_exit_attempts`
  (ladder/cap); `open_live_positions` no longer hides a position behind a rejected exit.
- **Price-escalation ladder:** base marketable buy-NO → slippage-buffered limit on re-attempts →
  optional best-effort market order (flag-off; market fields unconfirmed) → hold to settlement.
- **409 order_already_exists → success** (it landed); unique `client_order_id` per attempt
  (`exit:{strategy}:{ticker}:{n}`) avoids self-409. **Partial fills** sized to remaining qty.
  **Full Kalshi error body + payload logged** on rejection (the hook to finally RCA the CHI 400).
  **Bounded attempts** (default 3/day) then CRITICAL + hold to settlement.
- Config: `live_exit_slippage_cents` (0), `live_exit_use_market_fallback` (false),
  `live_exit_max_attempts` (3). 11 new exit tests. Still inert until tp_sl mode + live switches on.

### Exit verification #2 — hardening WORKS; root cause = bucket-market closes rejected
A live `tp_sl` round trip confirmed the hardened mechanics: re-attempts (`exit:...:2`, `:3`),
price escalation, **409 order_already_exists treated as success**, full error body captured,
bounded attempts. But the **close still fails on weather bucket markets**, and now we know why
the error body is uninformative: Kalshi returns a GENERIC `{"code":"invalid_parameters",
"message":"invalid parameters"}` with **no field detail** — even fully logged.

**The empirical pattern across all live tests is the real RCA:** *closing* a weather range-bucket
market (`...-B##.#` tickers) via API limit order is rejected `invalid_parameters` in BOTH
directions — `action=sell/side=yes` (test #1) AND `action=buy/side=no` (tests #2/#3) — while
*buying YES to open* the same bucket works fine, and the one THRESHOLD market (`...-T##`,
DEN-T69) accepted a buy-NO close. So it's specifically *closing the mutually-exclusive bucket
markets* that Kalshi rejects; the close method/code is not the problem.

**Implication:** live early-exit (TP/SL/break-even) is NOT viable on the weather bucket markets
with current knowledge (Kalshi's error gives no specifics; docs are 403-blocked). The reliable
path for the weather books is **hold-to-settlement** (the validated strategy — needs no closes).
Open question for later: try a market order, or ask Kalshi support, for the bucket-close param.

Ops note: the Railway env API had transient read-timeouts during cleanup; the timed-out writes
still landed (verified BOT_MODE=weather, KILL_SWITCH=true). Minor follow-up: mirror_entry should
also treat 409 as success (benign — Kalshi dedups on client_order_id). Baseline fully restored.

### The close was FIXABLE — wrong format, not the market (app screenshot proved it)
A screenshot of the Kalshi app cashing out a *bucket* position via "Slide to Sell" disproved the
"bucket markets can't be closed" theory. The working **pykalshi** SDK revealed the real issue: it
sends `action="sell", side="yes"` with **`count_fp` + `yes_price_dollars`** (strings) and **no
`type`/`no_price`** fields. Our close used integer `no_price`/`yes_price` + `type:"limit"`, which
Kalshi rejected for sells (generic `invalid_parameters`). Buys tolerated the integer format (entries
filled); sells did not — hence the asymmetry.
- **Fix:** `_place_exit` now SELLS the YES position like the app does — `action="sell", side="yes",
  count_fp="N.00", yes_price_dollars="0.NN"` at the bid (escalated re-attempts sell lower to force a
  fill; market fallback = `type="market"` sell). All exit tests updated to the new shape; green.
- **Live verification blocked:** Railway's env API had repeated read-timeouts, so the live tp_sl
  test couldn't be reliably configured (writes land but the config sequence kept getting cut). The
  fix is well-grounded (the app + a working SDK use exactly this format) and unit-tested, but a clean
  live confirmation is still pending. Worker left disarmed (weather, kill switch on).

### DEFINITIVE close finding — the exit now FIRES, but the API rejects BOTH closes on bucket markets
A clean live `tp_sl` test (LAX-B72.5, a favorite, ~11h to close so the market was definitively
OPEN) plus full payload evidence from `live_orders` finally settled the question — and overturned
the two prior theories ("sell-YES dollar format is the fix" AND "buy-NO works, CHI was just
closed-market"). Both were wrong. What the evidence actually shows:
- **The exit machinery is FIXED and now fires reliably.** Two bugs had prevented the close from
  ever being attempted on LAX: (1) the entry `409 order_already_exists` was mis-recorded as
  `rejected`, corrupting position tracking; (2) `open_live_positions` keyed off the (corrupted)
  entry row instead of the Kalshi position. Fixes: 409→`submitted` (success), and
  `open_live_positions` now driven by the **Kalshi position snapshot** (managed even if the entry
  row is corrupted). After deploy the worker correctly fired attempts `:1…:4` with retry,
  price-escalation, bounded attempts, and snapshot-as-truth — all working.
- **Kalshi rejects every close form we can build on a range-BUCKET market, even when OPEN:**
  - `sell-YES` integer limit `{side:yes,type:limit,count:1,action:sell,yes_price:68}` → `400
    invalid_parameters` (26JUN13).
  - `sell-YES` dollar `{side:yes,action:sell,count_fp:"1.00",yes_price_dollars:"0.74"}` → rejected
    (LAX 20:29–31, **market open**).
  - `buy-NO` integer limit `{side:no,type:limit,count:1,action:buy,no_price:30}` → rejected (LAX
    20:39, **market open**). This is the one that kills the "closed-market" theory: the LAX bucket
    was open for ~11h and buy-NO STILL failed.
- **The only close that has ever filled is buy-NO on a THRESHOLD market** (`-T##`): DEN-T69 buy-NO
  @17 executed. So the split is **threshold (`-T##`) closeable via buy-NO vs range-bucket
  (`-B##.#`) rejected in both directions** — a market-type distinction, not an order-format bug.
- **The app CAN close a bucket position** (user screenshot, "Slide to Sell"), so an accepted API
  form exists, but Kalshi's generic `invalid_parameters` (no field detail) + 403-blocked docs +
  SDKs that abstract the wire format mean we can't yet derive the exact bucket-close parameters.
- **Code state:** `_place_exit` reverted to the canonical **buy-NO** netting close (proven on
  threshold markets; the original Phase-6 design), marketable at `no_price = 100 − yes_bid`
  (+slippage on escalation), integer `count`/`type:"limit"`/`no_price`. All tests green.
- **Operational implication (unchanged & important):** the validated, edge-bearing weather books are
  **hold-to-settlement** and need NO closes — live trading is viable today on that basis. Live
  early-exit (TP/SL/break-even) on the bucket books is the one piece still blocked on the unknown
  bucket-close API form. Worker disarmed to the safe baseline (BOT_MODE=weather, KILL_SWITCH=true,
  LIVE_ENABLED=false); the open LAX position (~$0.80) holds to settlement.

### Order-group investigation — DISPROVEN; bucket==threshold structurally (read-only probe)
Added `scripts/kalshi_market_probe.py` (read-only, public Kalshi market-data, browser UA to
clear Cloudflare — no auth, no orders) and compared the LAX bucket that REJECTED closes
(`KXHIGHLAX-26JUN14-B72.5`) against the DEN threshold that ACCEPTED a buy-NO close
(`KXHIGHDEN-26JUN14-T69`). They are structurally identical for order purposes:
`market_type=binary`, `can_close_early=true`, `fractional_trading_enabled=true`,
`price_level_structure=linear_cent`, `price_ranges` step `0.01`, `response_price_units=usd_cent`,
`notional_value=$1`. The only diffs are economically irrelevant to orders (`strike_type`
between vs greater; `settlement_timer` 1800 vs 300). There is **no `order_group` field** on
markets or events, so order-groups are not involved.
- **Decisive:** the DEN market that closed fine is ALSO in a `mutually_exclusive=true` event —
  identical to the LAX bucket. So "mutually-exclusive bucket can't be closed" is FALSE; mutual
  exclusivity does not block closing. Buckets are closeable in principle.
- **So the LAX close rejections were not structural.** Two concrete culprits from the payload +
  spec evidence: (1) the sell-YES *dollar* attempts omitted the spec-required `type` field
  (`count_fp`/`yes_price_dollars` with no `type` → invalid_parameters); (2) every market is
  `fractional_trading_enabled` (sizes/OI come back as `*_fp`, prices as `*_dollars`), so the modern
  order path likely wants the fractional fields TOGETHER (`count_fp` + `*_price_dollars` +
  `type:"limit"`), not a half-integer/half-dollar mix. The one residual puzzle is the LAX buy-NO
  integer `no_price:30` (type=limit) rejection — same format that FILLED on DEN-T69 — which has no
  structural cause and is most likely a transient state/liquidity/balance edge at that instant.
- **Open spec question:** the authoritative `CreateOrderRequest` (official OpenAPI client) confirms
  `type` is required for limit orders, `Cent` = integer cents, and selling the held side is the
  documented close; docs.kalshi.com is Cloudflare-403 to WebFetch but GitHub-raw spec/model files
  are readable. Next definitive step is either capturing the app's real Slide-to-Sell request, or
  aligning the close to the full fractional format and re-testing once on a bucket.

### Fractional sell-YES retest — REJECTED too; all spec-derivable close forms exhausted
A fresh $1 round trip (h11 LAX favorite) cleanly tested the fractional sell-YES close (the app's
field shape WITH the spec-required `type`): `{action:sell, side:yes, type:limit, count_fp:"1.00",
yes_price_dollars:"0.XX"}`. The entry filled (and the entry-409→filled handling worked), then the
close fired as `exit:weather_fav_h11:...:1` and was **REJECTED `400 invalid_parameters`** — same as
every other form. So the fractional-format hypothesis is also wrong.

**Definitive close matrix on the weather RANGE-BUCKET markets (`-B##.#`, market OPEN):**
| close form | result |
|---|---|
| sell-YES integer (`type:limit, yes_price`) | REJECTED invalid_parameters |
| sell-YES fractional (`type:limit, count_fp, yes_price_dollars`) | REJECTED invalid_parameters |
| buy-NO integer (`type:limit, no_price`) | REJECTED invalid_parameters |
| buy-NO integer on a THRESHOLD market (`-T##`) | FILLED |

Every order matches Kalshi's published `CreateOrderRequest` spec, and the bucket market is
structurally identical to the threshold market that closes (same `market_type=binary`,
`mutually_exclusive`, `can_close_early`, `fractional_trading_enabled`, `linear_cent`). So the
blocker is neither order format nor order-groups nor market structure that we can see. The Kalshi
APP closes these buckets (user screenshot + the user's own app close of a LAX position, which
appeared in our fills as trades the bot never makes), so an accepted form EXISTS — we simply cannot
derive it from the spec/SDKs (generic error, 403-blocked docs). **The only remaining definitive path
is capturing the app's actual Slide-to-Sell request** (web devtools). Until then: the validated
weather books are hold-to-settlement (no closes needed) and live-tradeable on that basis; live
early-exit (TP/SL) on bucket markets stays blocked. Reusable read-only `kalshi_market_probe.py`
added for future structural checks. Worker disarmed to baseline; entry-window override reverted.

### EXHAUSTIVE close sweep — EVERY API order shape is rejected on bucket markets (conclusive)
A close-format discovery ladder (rejected orders are harmless no-ops that leave the position open,
so one deploy cycles many shapes) tried every order form the Kalshi API supports against a live,
OPEN LAX range-bucket position. **All rejected `400 invalid_parameters`:**

| # | close order shape | result |
|---|---|---|
| 1 | sell-yes limit, integer `yes_price` | ❌ |
| 2 | sell-yes limit, fractional `count_fp`+`yes_price_dollars` | ❌ |
| 3 | sell-yes limit + `sell_position_floor:0` | ❌ |
| 4 | sell-yes limit + `reduce_only:true` | ❌ |
| 5 | sell-yes **market** + `sell_position_floor:0` | ❌ |
| 6 | buy-no limit, integer `no_price` | ❌ |
| 7 | buy-no **market** + `buy_max_cost` | ❌ |

Meanwhile **buys to OPEN the same bucket fill fine**, and a **buy-no close fills on a THRESHOLD
market** (`-T##`). So this is not our order format, not a missing field, not order-groups, not
market structure (the bucket and threshold markets are byte-for-byte identical in the public
objects: `binary`, `mutually_exclusive`, `can_close_early`, `fractional_trading_enabled`,
`linear_cent`). **Conclusion: the Kalshi public order API does not accept ANY close/reduce order
on these range-bucket weather markets from this account, by any derivable shape.** The app closes
them (user's own app close appeared in our fills), so an accepted path exists via the app's
(privileged/internal) flow that the documented REST order endpoint does not expose to us.

**Decision/operational impact:** live early-exit (TP/SL/break-even) on the weather BUCKET books is
**not achievable via the API** with current knowledge — this is a Kalshi-side restriction, not a bot
bug. The validated, edge-bearing books are **hold-to-settlement** (need no closes) and are fully
live-tradeable. Everything ELSE in the live path is proven on real money: entry, marketable fill,
409→filled idempotency, reconcile (fills/positions/settlements), settlement P&L, the daily-loss
breaker, and exit *firing* (retry/escalation/snapshot-as-truth/bounded attempts). To unblock live
bucket exits, the remaining avenues are: capture the app's actual close request, or ask Kalshi
support why a spec-correct close on a range market returns `invalid_parameters`. The
`_exit_candidate` ladder + `kalshi_market_probe.py` remain in the tree for when a format is found.

### SOLVED — bucket close works via Kalshi's v1 user-scoped order endpoint (live-verified)
Captured the web app's Slide-to-Sell request (browser devtools) and it explained everything: the
app does NOT use the v2 `/trade-api/v2/portfolio/orders` endpoint the bot used — it posts to the
**v1 user-scoped endpoint** `POST /v1/users/{user_id}/orders` with a different schema:
```json
{"market_id":"<uuid>","user_side":"yes","side":"no","order_action":"sell","order_type":"market",
 "time_in_force":"immediate_or_cancel","count_fp":"1.00","price_dollars":"<100-yes_bid>",
 "sell_position_capped":true,"post_only":false,"expiration_unix_ts":0,"max_cost_cents":0}
```
That's why every v2 close (sell-yes / buy-no, all variants) was rejected `invalid_parameters` — the
v2 endpoint simply doesn't accept closes on these range-bucket markets; the v1 endpoint does.

Implemented + LIVE-VERIFIED on real money (a $1 LAX bucket round trip): entry filled → the v1
close FIRED and FILLED (fills table: `no sell, qty 2, @37` closing the position for **+$0.05**;
not settlement — that market didn't close until the next day). Key details learned the hard way:
- **Auth:** the bot's RSA API-key auth IS accepted on `/v1` (signs the full v1 path; same keys).
- **market_id:** the v1 order needs the market UUID, which the v2 objects don't expose and
  `/v1/markets/{ticker}` 404s — it lives on the **v1 event's nested markets**
  (`/v1/series/{series}/events/{event}`), matched by `ticker_name`.
- **price_dollars** must be the live NO-side price (`100 − yes_bid`, +slippage to cross); the
  app's `0.01` was specific to its ~99¢ position. An IOC that doesn't cross is canceled unfilled.
- **async fill:** the v1 IOC returns `status:"pending"` + an `order_id` and fills ~a cycle later;
  the fill carries that order_id, so reconcile resolves the order (`filled`/`canceled`).
- **`sell_position_capped:true`** closes the whole position safely (it even swept an older stuck
  position in one shot).

So the live early-exit (TP/SL/break-even) path now works on the weather BUCKET books, not just
hold-to-settlement. Full live loop proven on real money: entry → fill → reconcile → **API close →
fill → flat**. Worker disarmed to baseline after the test. (Security: the capture's request headers
contained a session cookie/CSRF/WAF token — ignored, never stored; operator advised to log out.)

### Fractional sizing — BUY confirmed live, but ONLY via the v1 endpoint (v2 rejects it)
Goal: spend an exact dollar amount (a "$3 position" = $3 of contracts, not `floor($3/price)`),
since the integer `count` floor leaves positions short of the target. Markets are
`fractional_trading_enabled` and `count_fp` accepts 0.01-contract increments.

**Hard live finding:** the **v2** `/portfolio/orders` endpoint rejects EVERY fractional
(`count_fp`) buy with `400 invalid_parameters` — verified across `yes_price` 91→99 (all fully
marketable, so it is NOT a pricing/marketability issue). v2 simply has no fractional support.
Fractional is a **v1-only** capability: the same `POST /v1/users/{user_id}/orders` endpoint the
close uses accepts `count_fp`. Rebuilt the isolated probe BUY as a **v1 fractional market buy** of
YES (mirroring the v1 close body: `order_action:"buy", side:"yes", user_side:"yes",
order_type:"market", count_fp, price_dollars=through-ask, max_cost_cents=cap`). LIVE-VERIFIED on
real money: MIA B93.5 filled at `count_fp 1.55` (avg 97¢, ~$1.50) — a true fractional position,
not rounded to a whole contract.

**Close side, fractional:** the v1 close already sizes to the exact fractional remainder
(`count_fp = quantity_fp`, tracked via the new `positions.quantity_fp`); unit-tested. In the live
probe it was NOT demonstrated end-to-end because the MIA position kept getting closed before
`_probe_close` ran. Diagnosing that uncovered a real bug (below).

### CRITICAL bug found + fixed — cycle-wide transaction rollback duplicated REAL orders
A controlled retry of the fractional round trip exposed it: a single `$1.50` probe buy on
`KXHIGHMIA-26JUN16-B92.5` became **3 real orders / ~12 contracts / $4.68** ("3 trades" on the
app) while only **ONE** `live_orders` row persisted (`quantity 4`). A 4-contract order cannot
fill 12 → multiple real orders were placed but their rows vanished. Root cause: `_run_live_cycle`
wraps the WHOLE cycle (`reconcile → manage_exits → run_probe → manage_open_positions →
tracker.run_once`) in ONE `session_scope` transaction. An order's intent row was only durable at
cycle-end commit, so a downstream error rolled it back **after** the order already hit Kalshi —
and the dedup / one-shot guards (which read that row) then re-fired a DUPLICATE real order next
cycle. This also explains the earlier "phantom" closes (a bot order whose row was rolled back).
**Fix:** in every order-placement path (`mirror_entry`, `_place_exit`, `_probe_buy`)
`session.commit()` the `pending` intent row **immediately, before the POST**, so it survives any
later cycle rollback and the dedup guard can never be fooled. Regression test
`test_intent_committed_before_post_survives_rollback_no_dup_order` simulates a post-POST rollback
and asserts no duplicate order fires (fails without the commit). The strategy's own once-per-
window entries did NOT duplicate (only the probe's every-cycle retry hammered the guard), so the
live DEN/LAX books were unaffected. The bot's fractional close is the same proven v1 path, sized
to the remainder.

**Fractional live ENTRY — now wired to v1 (`LIVE_FRACTIONAL=true` is safe).** `mirror_entry` was
reworked: when `live_fractional` is on it builds the shared `_v1_buy_body` (the same v1 fractional
MARKET-buy vocabulary the probe/close use) and POSTs via `create_v1_order`, instead of the v2
`count_fp` order Kalshi rejects. Sizing is `count_fp = dollars / price` capped by depth / risk /
`max_order_size`; the order is priced a couple cents through the ask with a cost cap; reconcile
leaves the async-filled v1 buy `submitted` (committed → dedup holds) and tracks the real size from
the position snapshot. Fail-closed: if `live_user_id`/`market_id` can't be resolved the entry is
skipped (logged), never silently mis-placed. So `LIVE_FRACTIONAL=true` now spends an exact dollar
cap (a "$3 position" = $3 of contracts) on the live books. Integer path (`live_fractional=false`)
unchanged.

**Reconcile hardening — no duplicate on an indeterminate-but-filled v1 entry.** A v1 order is
never visible in the v2 orders feed, so reconcile used to mark any `pending`/`unknown` order it
couldn't find there as `not_landed` — which would drop it out of the dedup set and let the next
cycle re-fire a DUPLICATE entry if the transient POST had actually landed. Now reconcile checks
Kalshi's freshly-fetched fills/positions first (`_executed_on_exchange`): any position or
same-action fill for the ticker → the order is resolved to `submitted` (committed → dedup holds);
only with NO execution evidence is it `not_landed` (a genuine non-landing, retry allowed).
Conservative by design — it favours "don't double-enter" over "never miss a retry". Regression
tests cover both directions (filled→submitted→no-dup; no-evidence→not_landed).

### Live trade log — DEN/LAX h-window favorites closed at ~99¢ (Jun 15, real money)
The two cross-validated favorite books filled at their windows and ran to ~99¢ (both winners),
then were closed early (operator call) via the bot's v1 buy-NO close rather than held to
settlement. Both positions went flat with matching no-sell fills, and the close intent rows now
PERSIST (post atomicity fix) — DEN's row resolved to `filled`; LAX's filled too (position flat +
6@1 fill) but its order row was cosmetically mislabeled `not_landed` (a known v1-close reconcile
gap: v1 orders aren't matched by `client_order_id` in the v2 orders feed, so a transient/async
close can be mis-resolved — harmless, the position snapshot is the authority for management):
| book | bucket | entry | exit (≈) | qty | gross P&L |
|------|--------|-------|----------|-----|-----------|
| `weather_fav` DEN h-win | `KXHIGHDEN-26JUN15-B81.5` | 48¢ | ~99¢ (no-sell @1¢) | 6 | ≈ +$3.0 |
| `weather_fav` LAX h-win | `KXHIGHLAX-26JUN15-B70.5` | 49¢ | ~99¢ (no-sell @1¢) | 6 | ≈ +$3.0 |

Net ≈ **+$6 gross** (minus entry+exit fees) on the two $3 favorite entries — the cross-validated
HIGH-favorite edge paying off live. (A separate 0.38-contract `LAX-B72.5` residual from an earlier
exit-sweep test had already hit the 3-attempt exit cap, so the bot holds it to settlement — ~$0.24,
immaterial.) This is also the first clean live demonstration that the bot's own close fires AND
leaves an audit record now that intent rows commit before the POST.

## Exit & sizing studies (live settled trades)

### Inverse view — rank by BACKFILL, check live (the trustworthy direction)
`weather_strategy_compare.py --top`. Ranking the favorite by the robust backfill sample
(n≥15/cell) and showing live alongside flips the picture from the live-first ranking:

- **The best backfill favorite cells are almost all HIGHS** (12 of the top 14; only NYC h20
  and PHIL h14 lows appear). The favorite edge the history supports lives in *highs*, not
  lows — the opposite of the live-first ranking, confirming the low-book live profit was
  small-sample luck.
- **Cross-validated winners (both samples positive, high backfill win%):** late-window (h8)
  highs in stable cities — DEN h8 (+3.8¢/68 backfill 96% · +3.7¢ live), MIA h8 (+3.6¢/100% ·
  +1.0¢), AUS h8 (+2.9¢/95% · +10.6¢), CHI h8 (+1.2¢/95% · +10.0¢) — plus LAX highs across
  windows (LAX h20 +3.3¢·+11¢, LAX h14 +3.3¢·+33.8¢) and NYC h14 (+1.5¢·+20.5¢).
- **Sign agreement: 11 of the top 14 backfill cells are also positive live.** The 3
  disagreements are all *early-window* continental highs (DEN h20, CHI h20, DEN h14: backfill
  + but live − on n=4) — early entry is less reliable (the high hasn't formed), and the *late*
  (h8) versions of the same cities cross-validate positive. Window matters: late > early for highs.

**Go-live implication (validated from both directions):** the defensible first live book is
**late-window (h8) high favorites in stable cities (DEN/MIA/AUS/CHI/LAX)** — high historical
win rates (95-100%) AND positive live — not the low favorite. Live per-cell n is still tiny
(1-5), so treat magnitudes as noisy; the SIGN agreement is what matters.

### Backfill vs live cross-validation — the low-book edge does NOT survive (caution!)
`weather_strategy_compare.py` replays the favorite three ways: backfill (Kalshi REST
history, n~350-480/cell), live realized paper P&L (n~17-28/cell), and best TP/SL on the
live trades. **The low books contradict between samples:**

| kind/window | backfill (large n) | live (small n) |
|---|---|---|
| low h20 | **−6.4¢ (468)** | +12.7¢ (19, 89%) |
| low h14 | −3.7¢ (346) | +5.7¢ (18) |
| low h8 | −6.6¢ (354) | +2.2¢ (17) |
| high h20 | +1.3¢ (481) | −4.2¢ (28) |
| high h8 | −0.9¢ (230) | +6.1¢ (18, 100%) |

The robust 25×-larger backfill says the **low favorite LOSES ~4-7¢**; the tiny recent live
sample says it's our best winner (+12.7¢). This is the small-sample/recent-regime trap in
action — the live "low fav h20 = best strategy" headline does **not** cross-validate. Trust
the backfill: do NOT seed live trading with the low favorite on the strength of the live
numbers alone.

**The only sign-consistent (both samples agree) positive signal is highs in stable
marine/subtropical cities:** high LAX backfill +3.2¢ / live +22.4¢; high MIA backfill +1.0¢
/ live +18.1¢. Everywhere else the two samples disagree (low cities: backfill all negative,
live all positive; DEN high +4.6¢ backfill but −2.6¢ live). TP/SL barely moves anything —
hold is best in 5 of 6 window cells (only high h20 benefits: tp=20 → +2.9¢), echoing the
exit-sweep finding. **Implication for go-live: the cross-validated candidate is high
LAX/MIA, not low fav.** Gather more live low-book settlements before trusting that edge.



### Best-strategy breakdown (live settled, by city × window × book)
`weather_pnl.py --best`. Robust picks (n ≥ 15, ranked by P&L/trade):
**low fav h20 = +12.7¢ (n19, 89%)** — the standout; then low nws/cal h14 +7.7¢ (n15),
high fav h8 +6.1¢ (n18, 100%), low fav h14 +5.7¢ (n18). Finer city cells are all n≈4 —
noise, not picks.

**City pattern worth noting (n 7–9):** the HIGH books are net negative *pooled*
(fav −2.2¢, nws −1.3¢) but strongly positive in **stable-climate cities** — high nws
MIA +33¢ (n9, 100%), cal MIA +28.6¢, cal CHI +25.9¢, fav LAX +22.4¢. Miami/LA highs are
low-variance (subtropical / marine layer) so forecasts nail them; the high-book edge is
**city-dependent**, echoing the #3 city×window result. Candidate: gate the high books to
stable cities (MIA/LAX/CHI), drop the volatile ones — needs more n before acting.

### Exit dynamics on the profitable (low) books — HOLD-TO-SETTLEMENT WINS
Replayed 158 settled low-book trades (`low_fav/nws/cal/pm` + `high_pm`) through their
recorded 15-min bid paths under a TP/SL grid, a **break-even stop** (arm at +gain,
then exit at entry if it falls back), and a **size/fee** sweep. `weather_exit_sweep.py`.

- **Hold beats every exit rule:** hold = **+5.3¢/trade**; the best TP-only is +3.6¢
  (−1.6¢ vs hold), and every stop-loss is brutal (hold/5¢SL = −10.9¢, −16¢ vs hold).
  Any SL gets whipsawed out of trades that dip intraday but settle YES.
- **Break-even also loses:** best BE config (arm 12¢ / TP 15¢) = +2.4¢, still −2.9¢
  vs hold; BE-only −0.9 to +0.5¢. Same reason — it exits near-certain winners on a
  transient dip back to entry.
- **Why:** the low-book edge is *outcome certainty* (thermometer lag — the overnight
  low is already locked, the bucket settles YES ~93%), not price momentum. Exiting
  early on a price wiggle only forfeits the settlement payout. **Verdict: keep the
  weather books hold-to-settlement; do not add TP/SL/BE to them.**
- **Size/fee:** per-contract P&L is +5.29¢ (qty 1) → +5.78¢ (qty 5) → +5.87¢ (qty
  100). Kalshi fees scale with qty, so size only amortizes the `ceil` rounding —
  a one-time ~+0.5¢ from qty 1→5, then flat. The real fee lever is *price* (fee =
  0.07·P·(1−P), tiny near 0/100), which the low favorite already enjoys. Trading ≥5
  contracts is a free ~0.5¢, but size is NOT the cost fix; holding + high-price entries are.

## Backfill structural-edge hunt (Apr–Jun history, 964 complete events)

**Scorecard (all 9 probes complete):** #3 city×window VALIDATED (→ live `cwin`);
#6 distribution overdispersion REAL (→ model calibration feature, not a book);
#2b mean-reversion REAL but sub-fee (→ model feature). Everything else priced or
dead — #1 overround, #2 persistence, #4 longshot, #5 momentum/convergence, #7 W→E
lead-lag (real physics, priced), #8 diurnal coupling (real physics, priced), #9
liquidity (mid honest even when thin). **Conclusion: Kalshi temperature markets
are remarkably efficient on everything derivable from price/public data — the only
positive EV is timing (`cwin`) and last-mile information lag (`obs`, `pm`), plus
sharpening the forecast model with #6.** Next build: promote the offline
distribution model into a live book (see "Build backlog" above).

### #9 — Liquidity-conditioned mispricing — REFUTED (mid is honest even when thin)
Is mispricing concentrated in illiquid buckets? At h12, grade each bucket's mid
vs its realized win-rate, binned by bid/ask spread and by candle volume.

- **The mid is well-calibrated in EVERY liquidity bin.** By spread: ≤2¢ → mid_err
  −0.7, 3–5¢ → +1.6, 6–10¢ → −1.2, >10¢ → −0.8 (even the widest-spread buckets win
  42% at a 42.9¢ mid). By volume: 0 → −0.4, 1–50 → +0.4, 51–500 → −1.2, >500 → −0.1
  (even zero-volume deep longshots, avg mid 4.6¢, win 4% — perfectly priced). Thin
  books do **not** create stale/sloppy mids.
- **Illiquidity is pure cost, not edge.** yes/no EV degrades monotonically as the
  spread widens: YES −2.4¢ (≤2¢ spread) → −11.3¢ (>10¢ spread); NO likewise. The
  spread is wide *because* there's no edge, and crossing it bleeds you. Thin
  buckets are the most expensive to touch, not the most profitable.

**Verdict:** the "sloppy pricing in thin markets" hypothesis fails outright —
Kalshi's resting quotes are honest even in the corners (zero-volume tails, >10¢
spreads) where sloppiness is most expected. Probe: `--analysis liquidity`.

### #8 — Diurnal-range coupling — strong coupling, PERFECTLY priced (no trade)
Within a city/day the overnight low (settles first, ~morning) and the afternoon
high are physically coupled; does the low inform the high beyond its price? (482
paired city-days)

- **Coupling is strong:** corr(high_anom, low_anom) = **+0.83** — a warm/cold
  airmass lifts both, as expected.
- **The market prices the spread almost perfectly:** implied diurnal range vs
  realized — mean(realized − implied) = **+0.04 °F**, corr = **+0.97**. The market
  nails the day's high-minus-low gap.
- **The low adds nothing to the high.** corr(high market_error, low anomaly) =
  **−0.07** (both realized *and* implied) — the high market has already priced
  whatever the low tells you. Even knowing the *realized* morning low (an obs-style
  entry) gives no predictive power on the high's error.
- **Tradeable test:** shifting the high by the low's anomaly (implied_high +
  k·low_anom) collapses win% 64% (k=0) → 29% → 18%, all negative; favorite 65% /
  −3.1¢. The k>0 "less-negative" pnl is the cheap-bucket artifact, not accuracy.

**Verdict:** strong real coupling, but Kalshi is *jointly* calibrated across a
city's high and low markets (range corr 0.97) — nothing to arb. Same theme. Probe:
`--analysis diurnal`.

### #7 — Cross-city W→E lead-lag — mechanism REAL, but FULLY PRICED (no trade)
Weather propagates west→east, so a western city's daily anomaly might lead an
eastern city's. Cities ordered by longitude (LAX→DEN→AUS→CHI→MIA→PHIL→NYC).

- **The physics is visible in the data.** Pooled corr(anomaly), downwind (west
  leads east) vs an upwind placebo (east leads west): lag 0 = +0.39 / +0.39
  (same-day cities just share regional weather — symmetric, as expected), but
  **lag 1 = +0.43 downwind vs +0.30 upwind**, **lag 2 = +0.40 vs +0.23**. The
  downwind-minus-upwind asymmetry (~+0.13–0.17 at lag 1–2) is the genuine
  fingerprint of airmasses moving W→E. This is a *real* detected signal — most
  lead-lag fishing finds nothing.
- **But the eastern market has already priced it.** corr(east market_error[d+lag],
  west anomaly[d]) = **−0.03 @ lag 1**, **−0.01 @ lag 2** (n≈2,800) — dead zero.
  By the time the east is at h12 the airmass is essentially overhead and the
  price reflects it (same public forecasts).
- **Tradeable test confirms no edge.** Shifting the east prediction by the western
  anomaly (pred = implied_east + k·west_anom[d−1]) *destroys* accuracy: win% 63%
  (k=0) → 25% (k=0.5) → 17% (k=1.0), all negative; favorite 65% / −2.8¢. The shift
  moves you off the (already-correct) implied center.

**Verdict:** we detected the propagation physics, but it's public information the
eastern market prices efficiently by h12. Same theme — derivable signal, no gap.
(Caveat: only tested at h12; the airmass is already imminent then. A much earlier
horizon, h36–48, is the only place this could conceivably be unpriced, but the
zero error-correlation even at lag 2 argues against it, and our backfill barely
reaches that far.) Probe: `--analysis leadlag`.

### #6 — Distribution shape / tail mispricing — REAL bias, NOT a standalone trade (→ model feature)
Normalize each event's h12 ladder into an implied PDF over bucket temperatures,
standardize the actual outcome (z = (actual − implied_mean) / implied_sd), and
check the shape. **This is the most actionable result so far.** (940 events)

- **Location is unbiased:** mean z = **−0.02 σ** — no systematic hot/cold lean in
  the market's implied mean.
- **The implied distribution is too WIDE (overdispersed):** SD(z) = **0.78** (< 1).
  Actual outcomes land *closer* to the implied mean than the prices say they
  should. **PIT tail mass lo=5% / hi=3%** (vs ~10%/10% if calibrated) confirms it
  — only ~8% of outcomes fall in the tails the market prices at ~20%. Reality is
  more predictable at h12 than Kalshi prices it; the **shoulders/near-tails are
  too rich and the center is too cheap.**
- **But the mispricing is below the cost floor.** Per implied-σ shell: center
  (0–0.5σ) wins 73% priced 72¢ (1¢ cheap), the 1.0–1.5σ shell wins 16% priced 18¢
  (2¢ rich), the far 1.5σ+ tail wins 2% priced 2.2¢ (fair — agrees with #4). The
  gaps are 1–2¢; after bid/ask + fees **every** YES and NO entry is negative
  (best cells ≈ −1.2¢). No naive buy-center / sell-shoulders book clears the floor.

**Verdict:** a *robust, mechanism-plausible* shape bias (retail spreads bets/lottery
tickets across tails; the market is slow to tighten its distribution as the day
constrains the outcome — same family as the `obs` thermometer-lag edge), but too
small to trade directly. **High value as a calibration feature for the forecast
model: trust the market's implied mean, then SHRINK its variance (~×0.78²).** The
resulting bucket probabilities should be better calibrated than the raw ladder,
especially by de-weighting the over-fat shoulders. This is the first finding that
feeds the edge model rather than just closing a door. Probe: `--analysis distshape`.

### #5 — Favorite momentum / convergence — REFUTED
Does the favorite's price drift predictably, and do bucket price moves trend?

- **Price moves are a random walk.** Autocorr of Δprice[h24→h12] vs Δprice[h12→h6]
  = **−0.03** (n=4798) — no momentum, no intraday reversion.
- **The favorite is fairly-to-richly priced and gets *worse* to buy as the day
  shortens:** buy-favorite-YES-hold-to-settle EV is **−1.8¢ @ h24**, **−3.5¢ @
  h12**, **−6.3¢ @ h6**. Win% rises (50→67→63%) but price rises faster — the
  market firms up *correctly* and the price already reflects it. No "favorite
  underpriced and converging" edge.
- **Chasing risers doesn't beat the favorite:** at h12 the biggest recent riser
  wins 63% (−4.3¢) vs the favorite's 67% (−3.5¢). The biggest faller wins only 6%
  — priced low, so its −2.8¢ isn't an edge either.

Corroborating note: the h24 favorite (−1.8¢) is the *least*-negative entry,
reinforcing the `cwin` finding that **earlier entry on highs beats late entry**
(late favorites pay up). That structural timing edge is already captured by the
city × window map; there is no separate momentum/convergence book to build.
Probe: `--analysis convergence`.

### #4 — Favorite-longshot bias (tail harvesting) — REFUTED
Bin every bucket at h12 by its mid price; sell cheap longshots (buy NO at
100−bid) and buy heavy favorites (YES at ask); grade on the actual winner.

Cheap tails are **well-calibrated** — actual win% sits on top of the implied
price (1–3¢ band wins 0.9%, 3–5¢ wins 4.3%, 5–10¢ wins 5.0%, 10–20¢ wins 13.6%).
After per-leg fees every sell-the-longshot band is negative (−0.3 to −2.4¢).
Heavy-favorite buys are non-monotone (80–90¢ −1.9¢, 90–95¢ +2.6¢, 95–100¢
−3.0¢) — the lone positive cell is noise at n≈90 (binary SE ~±5pts). **No
exploitable favorite-longshot bias from price alone.** Probe: `--analysis longshot`.

### #3 — City × entry-window map — VALIDATED (live as `cwin`)
Different cities firm up at different hours-to-close. A per-city window map beat a
flat window **out-of-sample**: pick the best window per city on the first 60% of
dates, evaluate on the last 40% → map **+2.9¢** vs flat **−2.4¢**. Significance +
month-stability + train/test holdout all checked. Built into the live `weather_cwin`
book (highs): `CHI:18, LAX:18, DEN:18, NYC:10, MIA:24, AUS:24, PHIL:10`.
Probe: `weather_window_sweep.py --validate`.

### #2 — Persistence / seasonal drift — PRICED (no trade)
Temperature is strongly autocorrelated, but the market already prices it. The
market's open-implied mean tracks yesterday's outcome essentially as well as the
actual does (implied corr 0.92 ≥ persistence corr 0.91); the naive "buy
yesterday's-outcome bucket at the open" strategy loses. A faint mean-reversion
residual exists (see below) but is too small to trade on its own.
Probe: `--analysis persistence`.

### #2b — Mean-reversion (fade after extreme) — REAL but TOO SMALL
After an anomalously hot/cold day the market slightly over-extrapolates;
corr(market_error, yesterday's anomaly) ≈ −0.18 pooled. Physically concentrated
in **continental cities** (DEN, PHIL, CHI) as expected. But the best fade
correction only adds **~+0.4¢/trade** — below the fee+noise floor to trade alone.
Kept as a candidate *model feature*, not a standalone book. Probe: `--analysis meanrev`.

### #1 — Ladder overround / sell-the-ladder arb — DEAD
In an N-bucket exclusive market the YES prices "should" sum to ~100¢. Across
every event × snapshot the bids sum < 100 on the tight CLOB; **0% of snapshots**
offered a risk-free sell-the-ladder credit net of per-leg fees. No structural vig
to harvest. Probe: `--analysis overround`.

---

## Earlier findings (pre-backfill, live paper books)

- **Simple TA has no edge.** Phases 3–3.5: over 1,000+ paper trades, `momentum`,
  `reversion`, and `buy_favorite` all lost ≈ −5¢/contract (the round-trip
  spread+fee cost floor). This is what drove the pivot to weather.
- **`buy_favorite` weather baseline** also bleeds the cost floor — confirms the
  ladder is fairly priced; an edge has to come from forecast/timing/cross-market
  information, not from buying the crowd's favorite.
- **NWS forecast & bias-corrected (`cal`) books** collect forecast data and trade
  the forecast bucket; the model-check harness grades the ensemble vs market
  (Brier/log-loss/EV) before any size goes on.
- **Polymarket cross (`pm`)** trades Kalshi toward a divergent Polymarket price
  on the same city (LAX/MIA/AUS, discovered via tags).
- **Obs-confirmed late entry (`obs`)** buys the bucket containing the day's
  running max/min once the real thermometer reading is in past a local cutoff
  hour and the ask is still ≤ cap — the thermometer-lag edge.

---
