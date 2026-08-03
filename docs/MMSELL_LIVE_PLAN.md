# MMSELL3 — live real-money test plan (staged, pre-registered)

> **⚠ ADDED 2026-07-26 — the mmsell10 live test runs with a PAPER TWIN in parallel.**
> `LIVE_STRATEGIES=mmsell10` now automatically starts a fresh paper book `mmsell10_pt` at the same
> instant, seeing the same candidates but priced/sized by the **live** knobs (maker no-bid + offset,
> `LIVE_MAX_ORDER_DOLLARS` sizing, `MMSELL_LIVE_MAX_OPEN_POSITIONS` cap, live spread gate). This
> matters because the incumbent `mmsell10` paper book carries months of history at 1-contract clips
> and a 200-position cap — comparing live against it conflates sample, regime, sizing and
> concurrency with the thing the test is for. The twin controls all four, so the twin-vs-live gap
> **is** the fill/adverse-selection cost this plan set out to measure (§2), measured directly rather
> than projected through the fill model's calibration.
>
> The read is `{"type":"script","name":"live_paper_parity"}`; its **matched-market** rows are the
> new load-bearing statistic — a per-contract gap on tickers *both* sides settled cannot be fill
> rate or adverse selection, so it would indict the simulator itself (and with it every paper gate
> in this repo, not just this book). Mechanism, gates and traps: `docs/LIVE_PAPER_TWIN.md`;
> arm-and-audit procedure: the `live-paper-parallel` skill. Retuning a live knob mid-test voids the
> epoch — start a new twin tag (`mmsell10_pt2`) rather than re-reading the old one.

> **⚠ PREREQUISITE for the NEXT live re-test (added 2026-07-22; re-test expected within ~1 week).**
> The next mmsell live test targets **mmsell10** — the one realizable candidate (`maxyes` price
> ceiling; `MMSELL_FILL_MODEL.md` §4), not the whole cohort. Before funding it, do **step 0: build
> the fill-realism collection.** Persist a per-cycle snapshot of every in-band mmsell **candidate**
> (not just held positions — `mmsell_position_ticks` already covers those): `market_ticker,
> captured_at, yes/no bid/ask, last, volume`, written by `MmSellTracker` (which already fetches the
> book to decide entry), plus a replay script. This converts today's live-*calibrated estimate*
> (drawn from one 359-trade window) into a **direct per-ticker fill measurement** that also reaches
> the rich price cells the estimate can't. Full rationale: `MMSELL_FILL_MODEL.md` §5 #2.
> Note: the 2026-07-22 Area-2 idea-model run confirmed order-flow-as-a-*signal* is dead on Kalshi
> (`docs/OFLOW_THESIS.md`), so this collection is worth building ONLY for fill realism, not alpha.

*Plan written 2026-07-12, before any live order is placed. The sizing, gates and kill criteria
below are **pre-registered** so the test can't be quietly re-scoped after the fact. Status:
**BUILT 2026-07-13 (inert) — awaiting demo dry-run, then Stage 1 funding. The live path now
exists (`LiveExecutor.mirror_mmsell_entry`, wired from `MmSellTracker`), all default-OFF.***

The one number that matters is still the north star: **+$100/month realized across the portfolio.**
This test does not chase that — it answers the single question that stands between the mmsell3 paper
edge and ever contributing to it: **does the maker edge survive real fills?**

---

## 1. Why mmsell3, and what paper already proved

`mmsell3` isolates the sweet spot of the favorite-longshot maker-sell edge: **sell yes on the
cheapest longshots (yes 5–10¢) = buy NO at the no-bid (~90–95¢), held to settlement.** It has
**passed its pre-registered paper gate** (BOOK_REGISTRY: n≥150, per-trade >+1.5¢ AND ≥ mmsell1/mmsell2).

Live paper pull, 2026-07-12 (`paper_trades`, `strategy='mmsell3'`, `status='settled'`, n=255, ~2 days):

| metric | value |
|---|---|
| win rate | **94.5%** |
| avg P&L / contract (net of fee) | **+1.96¢** |
| vs siblings | > mmsell1 (+1.19¢), mmsell2 (+1.21¢), control mmsell (+0.48¢) |
| entry price | buy NO @ **~91.5¢** (sell yes @ ~8.5¢) |
| per-trade SD | 22.9¢ |
| worst trade | −$0.96 · big losses (<−50¢) = **5.5%** of trades |
| avg fee | 1.0¢/contract · throughput **~127 trades/day** |
| peak concurrency | **68 positions / ~$62 capital** · avg 31.5 / ~$29 |

The distribution is strongly **negatively skewed**: ~94% of trades win a few cents, ~6% lose ~90¢.
The per-trade Sharpe is thin (~0.086), so the edge is real but only emerges over **many diversified
trades** — which is what sets both the bankroll and the test duration below.

## 2. The only thing this test can prove that paper cannot

Paper **assumes the resting no-bid fills for free.** Live, our order joins the queue at the no-bid
and two failure modes appear that paper is structurally blind to:

- **Fill rate / queue position** — a resting maker order may simply never fill. If the fill rate is
  low, the book is uninvestable regardless of its paper EV.
- **Adverse selection** — we tend to get filled exactly when an informed taker is lifting the other
  side (i.e. right before the longshot comes in). If so, the **live win rate on *filled* trades falls
  below the 94.5% paper baseline**, and the thin +1.96¢ edge can go negative.

Everything else (fees, settlement, void handling) paper already models well. **Fill realism and
adverse selection are the whole reason to risk money.**

## 3. What has to be built (mmsell has no live path today)

The live execution layer (`kalshi_bot/live/executor.py`) is production-grade and reused as-is for
safety, reconcile, exit management, and the `live_orders` / `fills` / `positions` tables (with real
`fee`). But it was built for the **weather books, which are YES-taker entries** (`best_yes_ask`,
`yes_price`, marketable/passive-below-ask). mmsell is the mirror trade — a **resting NO buy at the
no-bid, a maker order** — and the mmsell tracker never calls the executor at all. So the build is:

1. **Wire `MmSellTracker` → `LiveExecutor`.** Pass `live_executor` into the tracker (as
   `weather/tracker.py` does) and, right after `create_paper_trade` / `open_paper_position_for_trade`,
   call a live entry for allowlisted strategies only. Paper record stays the counterfactual shadow and
   is never rolled back by a live failure.
2. **Add a NO-maker entry path** to the executor (new branch or a small dedicated method). It must:
   rest a **`side="no"`, `action="buy"`, `type="limit"`, `no_price = best_no_bid`** order (join the
   queue — the pre-registered entry style; see §4), size to the dollar cap, and honor the dollar/depth/
   risk caps. The existing YES path is untouched.
3. **Reconcile + fill tracking already work** (`reconcile` pulls orders/fills/positions by
   `client_order_id`); confirm they resolve a resting NO order that fills partially or not at all, and
   that an **unfilled order is cancelled after `live_order_timeout_seconds`** and recorded, not left
   hanging.
4. **Scorecard script** `scripts/mmsell_live.py` (ops-allowlisted, read-only), mirroring
   `weather_pnl` / `weather_digest`. Metrics in §5.
5. **Risk-gate check.** The weather `risk.evaluate` gate rejects `spread > max_spread_cents` (5¢) —
   antithetical to the cheap-longshot maker edge (a wide spread is what the maker collects). So
   `mirror_mmsell_entry` uses its **own** mmsell-scoped gates (switches, per-ticker dedup,
   daily-loss, real balance, per-market exposure, a generous `MMSELL_LIVE_MAX_SPREAD_CENTS` sanity
   cap, and a `MMSELL_LIVE_MAX_OPEN_POSITIONS` cap) instead of the weather spread gate, and records
   an approved `risk_event` for the audit trail. The weather YES-taker path is untouched.

**Built (2026-07-13, all inert):** `LiveExecutor.mirror_mmsell_entry` (a resting maker buy-NO,
held to settlement), called from `MmSellTracker` right after each allowlisted paper open;
`repository.live_buy_exists_for_ticker` / `count_live_book_open`; `scripts/mmsell_live.py`
scorecard (ops-allowlisted). Ships **inert**: `LIVE_ENABLED=false`, `LIVE_STRATEGIES=""`,
`KILL_SWITCH=true`. Nothing places an order until an operator flips the switches **and** lists
`mmsell3`. The mmsell tracker only receives the executor under `BOT_MODE=live`.

**Kalshi V2 order endpoint (migrated 2026-07-13).** Kalshi deprecated `POST /portfolio/orders`
(it now returns `410 deprecated_v1_order_endpoint`), so the live path uses the current endpoint
`POST /trade-api/v2/portfolio/events/orders` (`client.create_events_order`). That endpoint quotes
everything from the **YES side**, so buying NO is expressed as **`side:"ask"`** (sell YES) at
`price = (100 − no_price)/100` **dollars**; `count` and `price` are **decimal strings** (numeric
types are rejected `400`); `client_order_id` is a fresh **UUID**; `time_in_force:"good_till_canceled"`
with **`post_only:true`** (a pure maker — a PostOnlyCrossCancel just means no fill that cycle, which
is safer than accidentally taking). Cancels use `DELETE /portfolio/events/orders/{id}`. The read/
reconcile endpoints (`get_orders`/`fills`/`positions`) were unaffected. Body shape verified against
recorded live requests. *(The weather YES-taker live path still targets the old endpoint and is
**not** migrated — it is inert/out of scope here and needs its own migration before weather goes live.)*

## 4. Config — Stage 1 (the ~$150 fill-realism test)

Pre-registered live knobs (env vars). Entry style is **rest at the no-bid / join the queue** —
faithful to the paper book and the maker edge, so the measured fill rate is the real one.

| knob | value | rationale |
|---|---|---|
| `BOT_MODE` | `live` | **required** — the executor (and the mmsell live mirror) only exist in live mode; the live cycle runs the same weather pipeline + reconcile loop |
| `LIVE_ENABLED` / `KILL_SWITCH` | `true` / `false` | the two master switches, flipped only to start |
| `LIVE_STRATEGIES` | `mmsell3` | **one-book allowlist** — nothing else can trade live |
| `MMSELL_LIVE_PRICE_OFFSET_CENTS` | `0` | join the queue AT the no-bid (Stage 1); `1` improves price to fill faster. Capped at the no-ask, never pays through |
| `LIVE_MAX_ORDER_DOLLARS` | `1.0` | ⇒ **1 contract** at ~92¢; the base risk unit |
| `MMSELL_LIVE_MAX_OPEN_POSITIONS` | `60` | bound concurrency near the observed paper peak (68) |
| `MMSELL_LIVE_MAX_SPREAD_CENTS` | `40` | generous sanity guard (NOT the weather 5¢ gate, which would reject the book) |
| `LIVE_ORDER_TIMEOUT_SECONDS` | `600` | cancel + record an unfilled resting order after 10 min |
| `LIVE_EXIT_MODE` | `settlement` | hold to settlement (exit sweep says TP/SL only hurt); NO positions auto-settle on Kalshi |
| `MAX_ORDER_SIZE` | `1` | hard per-order contract cap |
| `MAX_MARKET_EXPOSURE` | `2` | one small position per market |
| `MAX_TOTAL_EXPOSURE` | `120` | working-capital ceiling below the $150 bankroll |
| `MAX_DAILY_LOSS` + `LIVE_KILL_ON_DAILY_LOSS` | `15` / `true` | self-trip entries on a bad day |

Note: `LIVE_ENTRY_STYLE` / `LIVE_PASSIVE_OFFSET_CENTS` are the **weather** YES-taker knobs and do
not affect the mmsell maker path — the mmsell entry is always a resting BUY-NO at the no-bid, tuned
by `MMSELL_LIVE_PRICE_OFFSET_CENTS`.

**Bankroll: ~$150.** Working capital ≈ $63 (paper peak concurrency) + drawdown buffer ≈ $85. At ~92¢/
contract that's ~160 contracts of buying power — comfortably above the ~68 peak, so the book is
**never capital-blocked** (a block would bias the sample by stopping entries exactly when it
shouldn't). Total downside is psychologically trivial; expected P&L over the test is immaterial
(~$10–30). **The deliverable is the fill-realism read, not the profit.**

## 5. Metrics captured (the scorecard)

Most columns already exist (`fills.fee`, `live_orders`, `positions`, `account_snapshots`). The
scorecard reports, live vs the paper shadow:

1. **Fill rate & time-to-fill** — orders placed vs filled vs cancelled/expired; how long they rested.
   *(headline unknown)*
2. **Adverse selection** — realized win-rate on *filled* live trades vs paper 94.5%. *(the decider)*
3. **Effective slippage** — fill price vs intended no-bid; opportunity cost of unfilled orders.
4. **Actual fees** — `fills.fee`/contract vs modeled, sliced by clip size (tunes size↔fee↔fill).
5. **Fill selection bias** — price/odds distribution of *filled* vs *attempted* entries.
6. **Realized P&L / contract** live vs paper +1.96¢, with the paper book running as the counterfactual.
7. **Capital utilization / concurrency / drawdown** via `account_snapshots`; void handling.

The scorecard runs through the ops channel (`{"type":"script","name":"mmsell_live"}`) and folds into
the daily `digest`.

## 6. Pre-registered gates

**Stage 1 — fill realism (1 contract, ~$150).** Run until **≥150 filled live round-trips** (or 4 weeks,
whichever first). ADVANCE to Stage 2 only if **both**:
- **fill rate ≥ 50%** of placed resting orders (else the queue is unfavorable and the book is
  impractical at the no-bid — retry once at `LIVE_PASSIVE_OFFSET_CENTS=1` before abandoning), AND
- **live win-rate on filled trades ≥ 90%** (within ~1σ of the 94.5% paper baseline; a drop to <90%
  is the adverse-selection signature → **do not scale**, diagnose or shelve).

**Stage 2 — measure the edge (2–3 contracts, ~$300–500).** Larger clips amortize the 1¢ fee ceil
(fee/contract drops from ~1.0¢ toward ~0.6¢ at 5–10 contracts) and gather fills faster. Run until
**≥500 filled round-trips**. KEEP the live book (and let it contribute to the $100/mo goal) only if
**realized P&L/contract > +1.0¢ net of real fees at n≥500** AND fill-rate/win-rate hold. Otherwise
**shelve live** and keep mmsell3 as a paper book.

**Hard kill (any stage):** realized live P&L ≤ −$25 cumulative, or `MAX_DAILY_LOSS` trips twice in a
week → flip `LIVE_ENABLED=false`, hold open positions to settlement, review before resuming.

## 7. Rollout sequence

1. **Build** §3 behind all switches OFF; unit-test the NO-maker order body + the scorecard. Merge (still inert).
2. **Demo dry-run** — run `docs/LIVE_DRYRUN.md` against `KALSHI_ENV=demo` for the NO-maker path:
   confirm a resting `side="no"` limit places, rests, fills/cancels, and reconciles with the real
   Kalshi response shapes. **No real money until this passes.**
3. **Stage 1** — production, `LIVE_STRATEGIES=mmsell3`, config §4, ~$150 funded. Watch the daily
   digest + scorecard; §6 Stage-1 gate decides advance/retry/shelve.
4. **Stage 2** — scale per §6 only on a clean Stage-1 pass.
5. Update **BOOK_REGISTRY.md** `mmsell3` status at each transition (paper → live-stage1 → live-stage2 /
   shelved), and log decisions in RESEARCH_JOURNAL.md.

## 8. Safety / rollback

Fail-closed throughout: any gate/switch failure places nothing. `LIVE_ENABLED=false` (or
`KILL_SWITCH=true`) instantly halts all new live entries; open positions hold to settlement (they
can't be worse than their entry cost — max loss per contract is the ~92¢ already committed). The paper
`mmsell3` book keeps running unchanged as the live-vs-paper counterfactual for the entire test.

## 9. Wind-down / closeout (added 2026-07-19 — Stage 1 concluded, see `docs/MMSELL_LIVE_POSTMORTEM.md`)

mmsell was built **hold-to-settlement only** — §8's "open positions hold to settlement" was the
plan until it wasn't. `LiveExecutor.close_mmsell_positions` (config: `mmsell_closeout_enabled`,
`mmsell_closeout_strategies`) is the one-shot mechanism added to actually exit early: it buys YES
at the current ask (marketable IOC, crosses the spread — a close must guarantee execution, not
rest as a maker) to flatten every open NO position for the listed strategy prefixes, tagged
`strategy="<tag>_closeout"` and `client_order_id="closeout:..."` so closes are unambiguous in
`live_orders` next to ordinary entries.

**The sequencing matters and is easy to get backwards** — closing positions requires the client's
order-placement guard to be open (`bot_mode=="live" and not kill_switch`), so `KILL_SWITCH=true`
would block the CLOSE orders too, not just new entries:

1. Stop new entries via the **allowlist**, not the kill switch: set `LIVE_STRATEGIES=""` (or
   remove the target tag). Leave `KILL_SWITCH=false` / `LIVE_ENABLED=true` — order placement must
   stay live for the closes to reach Kalshi.
2. Set `MMSELL_CLOSEOUT_ENABLED=true` and `MMSELL_CLOSEOUT_STRATEGIES=<tag>` (e.g. `mmsell3`) and
   redeploy. Every live cycle thereafter closes whatever's still open.
3. Confirm flat via the ops channel — `live_orders` where `strategy='<tag>_closeout'` all
   `filled`/`submitted`, and zero open positions.
4. **Only now** set `KILL_SWITCH=true` for the final, permanent, defense-in-depth stop.
5. Set `MMSELL_CLOSEOUT_ENABLED=false`. See below for why this step is not optional.

Skipping step 1 (leaving the entry allowlist live) races new entries against the closeout in the
same cycle. Reaching for `KILL_SWITCH=true` first (before positions are flat) blocks the closes
from executing at all — the fail-closed order-placement guard doesn't distinguish "close" from
"open" intent, only that live money is moving.

### What actually happened, and the bounds added because of it (2026-08-03)

The wind-down ran steps 1–4 but not 5, so the flag stayed on past `KILL_SWITCH=true`. "One-shot"
described the intent, not the code: the closeout re-derives its work list from **Kalshi's position
snapshot every cycle**, so a position it cannot close comes back every cycle and is tried again.
The claim it was "self-limiting" holds only when the closes actually *fill*.

Result: **1,942 dead `live_orders` rows across 8 tickers**, retried 160–644 times each — 1,913 of
them the client's own `"Live order placement is disabled"` rejection (step 4 done before step 5),
the rest markets that had already resolved and could never be closed at all. No money moved; each
attempt cost a `pending` row, an orderbook fetch and a POST.

Three guards now bound it, in `LiveExecutor` (all shared with the identical `theta` path):

| guard | what it stops |
| --- | --- |
| `_closeout_can_place` | Refuses to start the loop when `KILL_SWITCH=true` — logs one line naming both fixes instead of writing an order per position per cycle. |
| `_closeout_market_untradeable` | Skips markets whose status is no longer `active`/`open`: they cash-settle on their own, so there is nothing to close. Fail-soft — a lookup error proceeds, so a flaky API never stalls a wind-down. |
| `_closeout_exhausted` | Gives up on a ticker after `MMSELL_CLOSEOUT_MAX_ATTEMPTS_PER_TICKER` (default 5) close orders, counting fills-that-didn't-happen as well as rejections; logged once per process. `0` restores unbounded retries. |

These bound the blast radius; they don't replace step 5. A closeout left enabled still re-checks
every position every cycle — turn it off when the book is flat.
