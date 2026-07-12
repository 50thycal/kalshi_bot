# MMSELL3 — live real-money test plan (staged, pre-registered)

*Plan written 2026-07-12, before any live order is placed. The sizing, gates and kill criteria
below are **pre-registered** so the test can't be quietly re-scoped after the fact. Status:
**PLAN — awaiting build + demo dry-run. No live path exists for mmsell yet.***

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
5. **Risk-gate check.** `risk.evaluate(for_paper=False)` runs spread/liquidity/exposure gates tuned for
   weather; confirm they don't reject every cheap wide-spread longshot (or add a mmsell-scoped path).
   A gate that silently blocks all entries would look like "zero fills" and mislead the test.

All of it ships **inert**: `live_enabled=false`, `live_strategies=""`, `KILL_SWITCH=true`. Nothing
places an order until an operator flips the switches **and** lists `mmsell3`.

## 4. Config — Stage 1 (the ~$150 fill-realism test)

Pre-registered live knobs (env vars). Entry style is **rest at the no-bid / join the queue** —
faithful to the paper book and the maker edge, so the measured fill rate is the real one.

| knob | value | rationale |
|---|---|---|
| `BOT_MODE` | `weather` | mmsell rides the weather/live cycle; keeps the existing reconcile loop |
| `LIVE_ENABLED` / `KILL_SWITCH` | `true` / `false` | the two master switches, flipped only to start |
| `LIVE_STRATEGIES` | `mmsell3` | **one-book allowlist** — nothing else can trade live |
| `LIVE_ENTRY_STYLE` | `passive` | rest at the no-bid, do not cross the spread (maker) |
| `LIVE_PASSIVE_OFFSET_CENTS` | `0` | join the queue AT the no-bid (no price improvement in Stage 1) |
| `LIVE_MAX_ORDER_DOLLARS` | `~1.0` | ⇒ **1 contract** at ~92¢; the base risk unit |
| `LIVE_ORDER_TIMEOUT_SECONDS` | `600` | cancel + record an unfilled resting order after 10 min |
| `LIVE_EXIT_MODE` | `settlement` | hold to settlement (exit sweep says TP/SL only hurt) |
| `MAX_ORDER_SIZE` | `1` | hard per-order contract cap |
| `MAX_MARKET_EXPOSURE` | `~2` | one small position per market |
| `MAX_TOTAL_EXPOSURE` | `~120` | working-capital ceiling below the $150 bankroll |
| `MAX_DAILY_LOSS` + `LIVE_KILL_ON_DAILY_LOSS` | `~15` / `true` | self-trip entries on a bad day |
| a mmsell live max-open cap | `~60` | bound concurrency near the observed paper peak (68) |

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
