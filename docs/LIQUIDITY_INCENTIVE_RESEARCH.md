# Liquidity-incentive shadow MM — research record (architecture, external repos, API, semantics)

**Written 2026-09-16** for `WS-020` / `docs/LIQUIDITY_INCENTIVE_THESIS.md`. This is the durable
record of what was inspected before anything was built, and of what was *verified* versus
*assumed*. Every claim below names its source; the sandbox cannot reach `kalshi.com`,
`help.kalshi.com`, `docs.kalshi.com` or `cftc.gov` (egress-blocked), so where a sentence rests on
a search-engine excerpt of those pages rather than the page itself, it says so.

## 1. Repository architecture assessment (what exists, what was reused)

Measured on head `4faff1b` (WS-019 execution telemetry merged the same day).

| need | what exists | reused / decision |
|---|---|---|
| Kalshi REST client | `kalshi/client.py`: signed `_request`, typed GET methods, cursor idiom (`iter_markets`) | Added `get_incentive_programs` / `iter_incentive_programs` / `get_series` as typed methods with the measured shape in the docstring. No public generic GET existed. |
| WebSocket collector | `execution/collector.py` (WS-019): `CollectorState` + `TelemetryThread`, `orderbook_delta` with `use_yes_price`, `trade`, per-sid `seq` check, throttled raw persistence, GET-only wrapper | **Same two-layer construction, separate instance.** The execution collector's tracked set is *markets with a resting live order* and it runs only in `BOT_MODE=live`; the shadow needs arbitrary tickers in any mode. Copying the shape rather than extending the instance keeps WS-019's live-money instrument untouched. `LocalBook`, `parse.py` and the `ReadOnlyKalshi` WS plumbing are imported, not copied. |
| Order-book / trade tape storage | `execution_book_events` / `execution_trade_events` (WS-019, live markets only); `market_snapshots` / `orderbook_snapshots` (scanner universe only); no retention anywhere | Own tables `incentive_book_events` / `incentive_trade_events` with the identical shape and YES-scale convention, so the two instruments never share rows and a replay tool can read either. Volume bounded at write time (per-minute cap), as everywhere else. |
| Fee model | `paper/engine.py::kalshi_fee` (taker 0.07 ceil-per-order; maker 0.0175 no ceiling); `docs/MMSELL_FEE_RECON.md` measured **~0.01c/contract maker** on MMSELL series (n=569), i.e. effectively zero; the fee formula is a `FEE_MODEL` platform component | **Not changed** (a change is a Platform Revision). The shadow resolves a per-market `FeeRule` from the SERIES object's `fee_type` / `fee_multiplier` at first sight and stores it on the program row; the default when the lookup fails is maker 0 / taker 7% *and says so in `source`*. |
| Market metadata | `GET /markets/{t}` (`title`, `status`, `close_time`, `result`) | Resolved once per new terms row; also used by the settlement pass. |
| Existing incentive code | **None.** `grep -ri incentive` over `kalshi_bot/`, `scripts/`, `docs/` finds only evo "reward" (fitness) and four places asserting "no maker rebate exists" | Greenfield module `kalshi_bot/liquidity_incentive/`. The "no rebate" claim is about *fee rebates*; the incentive program is a separate subsidy and does not contradict it. |
| Queue telemetry | `live_order_queue_ticks` (Kalshi's `queue_position_fp`, real orders only); `LocalBook.features_for` (hypothetical level features) | A hypothetical order has no Kalshi queue position, so the shadow reconstructs "contracts ahead" from the book (depth at our level at placement) and updates it from deltas — the queue-aware fill model. Documented as a model, not a measurement. |
| Dashboard | `livedash` (stdlib `http.server`, GET-only, one static page per view; `/execution` precedent) | `/incentives` page + `/api/incentives/{active,history,headline}`; data layer in `liquidity_incentive/report.py`. |
| Research framework / XOS | `_packages()` registry; WS-015 shadow precedent (probe deployment + scope tag); WS-017 / WS-019 precedent (collector or probe **without** XOS registration because no `paper_trades` tag writes) | **No XOS package.** Under `NEW_ONLY` a registration is what admits a *tag* to trade; this instrument trades nothing and writes only its own tables. The pre-registration lives in the thesis doc; a live POC would be a new XOS experiment registered then (`arm_live_canary`, hard stop). |
| Paper simulation | `paper/engine.py`, `fill_calibration.py` (price-cell P(fill) from live MMSELL fills, keyed by yes-cent) | Not reused: the calibration is for one-sided NO bids at 5–13c on sports/contests; a two-sided quote at 30–70c on incentivized markets is a different population. Instead three explicit fill models replay the real tape. |
| Worker | one synchronous loop; the only thread is WS-019's | Second daemon thread, any mode, `LIQUIDITY_INCENTIVE_SHADOW_ENABLED` (default **off**), started/stopped beside the telemetry thread. Discovery runs inside the thread. |
| Ops | `scripts/ops_runner.py::ALLOWED_SCRIPTS`; `scripts/railway_env.py::ALLOWED_VARS` | `liquidity_incentive_report` script; the enable/cadence/bound vars allowlisted. Policies and tiers are deliberately **not** settable from ops — they are the pre-registration. |

## 2. External-repo findings

Cloned 2026-09-16 into the session scratchpad (not vendored):

**`gallantfoxapp/kalshi-incentives`** (`46a2ceb`, 943 lines, stdlib). *Reused conceptually:* the
endpoint contract (`GET /incentive_programs`, list key `incentive_programs`, cursor `next_cursor`,
statuses `active|upcoming|closed|paid_out`, types `liquidity|volume`), the field set, the fee
arithmetic (`ceil_to_cent(rate × n × p × (1−p))` with a `round(x, 10)` pre-step so
`0.07×100×0.25 = 1.7500000000000002` stays 1.75), and its numeric test anchors (1.75 / 0.02 /
0.44 / 0.88 / p↔1−p symmetry), now reproduced in `tests/test_liquidity_incentive_fees.py`.
*Avoided:* it prints `period_reward` raw (a unit bug: 1,000,000 is $100, not $1,000,000); it
matches programs by series prefix (programs are per-market and time-bounded); it defaults an
unknown fill to taker; it has **no** scoring, reference-price or reward-reconciliation logic.

**`recallnet/skills-pred-market-rewards`** (`1d67243`): README + SKILL.md only, no code — a
prompt for a hosted API. Its backing app, **`recallnet/sponsored-rewards-tracker`** (`2ae1739`),
holds the only real Kalshi code in that family: a typed `RawIncentiveProgram`
(`period_reward: number`, `discount_factor_bps: number|null`, `target_size_fp: string|null`), the
**centi-cents → USD** conversion (`/10_000`), and per-status aggregation. *Avoided:* no persisted
history (it re-fetches on every request); "$1.43M paid out" sums *active* pools; its
`opportunities.ts` score is a Polymarket log-scaled "bigness" ranker with **no competition term**,
i.e. the opposite of an under-competition screen.

**`vinilpolepalli/quantfirm` PR #84** (found while verifying the docs; `research/kalshi_incentives.md`,
dated 2026-09-16 — someone else's same-day scan, not a source of truth). *Useful priors it
recorded, to be tested rather than believed:* board pays ~$106k/day against ~$17M resting
(0.62%/day); Target Size is 1,000 in ~98% of programs and Discount Factor 0.50 in ~97%; reward
yield decays ~119× from a program's first two hours to its second week as competition arrives;
the Reference Price is set by *cumulative depth*, so a thin touch over a fat 1–2c layer puts the
reference at 1–2c (their `KX30YMORTW` example). Its scoring reading agrees with §4 below. It also
identifies the conduct risk: resting size 23c off the touch purely to harvest the subsidy "is not
what the program is for" — which is exactly the handoff's guardrail and why our policies quote
where we would accept a fill.

**`UTXOnly/oddrip`** vendors Kalshi's OpenAPI **3.30.0** (`openapi.yaml`). Used only to read the
schema (§3); nothing else from it.

## 3. Current Kalshi incentive API — verified

From the vendored OpenAPI 3.30.0 spec (the docs host is egress-blocked; the spec is Kalshi's
own file, redistributed), cross-checked against both external clients above:

```
GET /trade-api/v2/incentive_programs
  status   all|active|upcoming|closed|paid_out   (default all)
  type     all|liquidity|volume|margin_maker_volume|margin_taker_volume
  incentive_description   exact-match filter
  limit    1..10000 (default 100)     cursor
→ { incentive_programs: [IncentiveProgram], next_cursor }

IncentiveProgram (required: id, market_id, market_ticker, incentive_type, incentive_description,
                  start_date, end_date, period_reward, paid_out)
  period_reward          int64   "Total reward for the period in centi-cents"
  discount_factor_bps    int32|null  "Discount factor in basis points"
  target_size_fp         FixedPointCount|null  (2-decimal contract string, e.g. "1000.00")
  max_reward_per_account int64|null  "Maximum reward per account in centi-cents"
```

Unauthenticated on Kalshi's side (gallantfox docstring); our client signs every request anyway.
No scoring parameter beyond Target Size and Discount Factor is exposed. **The margin_* types and
`max_reward_per_account` are newer than either external client** — the collector stores unknown
fields in `extra_params_json` and types the per-account cap.

Fee structure per series (same spec): `Series.fee_type ∈ {quadratic, quadratic_with_maker_fees,
quadratic_with_combo_maker_fees, flat}` and `Series.fee_multiplier` (double); scheduled changes
via `GET /series/fee_changes` and `GET /events/fee_changes`; `Market.fee_waiver_expiration_time`.
`quadratic` is "the General Trading Fees Table" (taker only); the `*_with_maker_fees` types charge
the published maker coefficient. **Not verified:** whether `fee_multiplier` scales maker and taker
identically (assumed yes), and the current PDF's per-series list (blocked). The reconciliation in
`docs/MMSELL_FEE_RECON.md` remains the only *measured* maker-fee figure in the repo.

WebSocket: `orderbook_delta` (with `use_yes_price`), `trade`, `market_lifecycle_v2` — field names
as WS-019 verified them (`docs/MMSELL_QUEUE_FILL_TELEMETRY.md` §3); nothing new needed.

## 4. Scoring semantics — published rules vs our assumptions

Published (help-center article 13823851 via search excerpts; CFTC filings `rules09082530054`
Aug 2025 and `rules02112639183` Feb 2026 amendment effective 2026-02-28 — titles and the
"Snapshot Liquidity Provider Score" term confirmed, bodies not readable from the sandbox):

| rule | text as excerpted | code |
|---|---|---|
| R1 | snapshots once per second | `RULE_SNAPSHOT_SECONDS` |
| R2 | a snapshot is excluded unless **both** sides hold ≥ Target Size; the period reward is prorated by qualifying/total snapshots — "$100 reward, 10,000 snapshots, 8,000 qualifying; 20% share earns 20% × $100 × (8,000/10,000) = **$16.00**" | `worked_example_reward` (test) |
| R3 | Reference Price: walk down from the best bid to the first level at which cumulative resting size reaches **one fifth** of Target Size | `side_score` |
| R4 | raw score = size × distance multiplier; at/above the reference 1.0; k ticks below = DiscountFactor^k; Discount Factor ≤ 1.00 (1.00 = no penalty) | `order_multiplier` |
| R5 | per side, each order ÷ total raw score of all qualifying orders on that side; snapshot score = yes share + no share (max 2.0) | `estimate` |
| — | Target Size is "more than 100 and fewer than 20,000 contracts"; daily pools "$10–$1,000"; eligible participants exclude affiliates, members with a Market Maker Agreement, and IB/FCM customers | recorded; the account is none of those |

**Assumptions the excerpts do not settle** (each labelled in `scoring.py`, each a question for the
first week of data and for the live rewards page if a POC ever runs):

- **A1** "Target Size resting on a side" = total resting contracts on that side at any price.
- **A2** the scoring period is `[start_date, end_date]`, so reward accrues uniformly per second.
- **A3** every resting order is a "qualifying order" regardless of price (if a band applies, our
  field denominator is too large → our share is *under*-estimated, the conservative direction).
- **A4** our hypothetical size is added to the field and counts toward the Target Size test.
- **A5** the settlement of an unmatched leg pays 100/0 on `result` (`yes`/`no`) — scalar markets
  are excluded from the shadow's settlement pass (result would not be `yes`/`no`).

**Derived vs observed** is enforced by column naming: `est_*` columns and `ScoreEstimate` fields
are the model; everything else on a row came from a Kalshi payload or the local book.

## 5. What the first days of data must check before any number is believed

1. `period_reward` unit: the first discovery row's `period_reward_usd` against the value shown
   on `kalshi.com/incentives` for the same market (expect $10–$1,000/day-scale pools).
2. Target Size / Discount Factor distributions (quantfirm's 1,000 / 0.50 modes).
3. The Reference Price the model computes against the projected-reward estimate Kalshi shows a
   signed-in user (the handoff's second retail report) — the one *external* check on R3–R5.
4. Whether our `est_reward_per_hour` for a program summed across the field is close to the
   program's per-hour accrual (it is by construction ≤; a large gap means A3 bites).
