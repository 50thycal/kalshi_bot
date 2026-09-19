# Discretionary desk — three researched picks a day, placed by the operator

**Status:** active operating model, opened 2026-09-19 on an operator request and adopted the same day (`DEC-017`). Nothing here
changes an Experiment OS state, a gate, a live safeguard or the arming path. The desk trades
through the **Kalshi app, by the operator's hand**, outside the worker — see §2 for why.

## 1. The request, restated

> Instead of building a bot, you tell me what to buy based on your research. Full-time job
> researching markets, find an edge by any means necessary (ethical). Three trades a day,
> only where we are most confident. Capture every failure. Learn from the data in the repo.
> Skip probes and paper; go straight to live, one dollar first.

The desk exists to answer one question the automated books never could: **can a human-shaped
research process, with unlimited context and no fill model, find edges the tape-driven books
missed?** Every prior book was a rule applied to price history. This is the opposite bet —
information the market has not priced, found by reading.

## 2. Why the operator places the order, not the worker

The repo's live path cannot place a discretionary order and is built so that it never can:

- Every order the worker sends is selected by a strategy's own scan. No path accepts a ticker
  from outside. The one exception (`LIVE_PROBE`) writes the `probe` tag, which Experiment OS
  enforcement refuses under `NEW_ONLY` because that experiment is RETIRED.
- Arming anything is a hard stop (`docs/STANDING_AUTHORIZATIONS.md`), and a registered live
  book requires a paper twin, a frozen version and a pre-registered risk envelope. Building a
  "manual order" path would mean weakening exactly the safeguards the 2026-08-15 Lmmsell
  failure is the reason for.
- The Kalshi app is already the right instrument for one human-sized order: it is outside every
  worker control, it needs no code, and it is where the fill actually happens.

So the division of labour is: **the desk researches and writes the pick; the operator reads it,
decides, and taps buy.** The worker keeps running its own books untouched.

## 3. Daily cadence

1. **Board read** (ops channel, public data): `kalshi_desk_board` — every open market closing
   inside the horizon, ranked by 24h volume, with spread and the taker fee at the ask. Then
   `--ticker` on each candidate for the rules text, the resting book and the last trades;
   `desk_fetch` on the settlement source the rules name.
2. **Research** — web search on the underlying question (the rules-defined settlement source
   first, then the news flow), the repo's own settled history where the series has one
   (`backfill_regime_markets`, `paper_trades`, `weather_forecast_outcomes`), and the base
   rates in `docs/MMSELL_MARKET_TYPES.md`.
3. **Picks** — at most three, each written to the ledger **before** the operator acts, in the
   format of §5. A day with zero picks is a valid day and is logged as one.
4. **Settlement** — every pick is graded on its settlement, and every loss gets a postmortem
   line in `docs/desk/POSTMORTEMS.md` tagged with the failure class from §4.

## 4. What the record already says — carried in as rules

The catalogue behind these rules is `docs/BOOK_REGISTRY.md`, `docs/RESEARCH_JOURNAL.md` and
`docs/IDEA_MODEL_SCORECARD.md`. Compressed to what binds a discretionary picker:

| # | Rule | The evidence it comes from |
|---|---|---|
| R1 | **Price alone is never the thesis.** | Every price-history book on a mature market died: TA books −5¢/contract, favorite-longshot fade flat, momentum/convergence random walk, order flow corr +0.008 on 823k trades. |
| R2 | **The pick names the settlement source and what it will say.** | The only family with signal is "mechanics blindness": the outcome is knowable from the rules-defined source before the crowd reads it (PIN15 passed; source-inattention pins died once the source went public). |
| R3 | **Cost floor first.** A taker at price P pays ceil(7 × P(1−P)) ¢ per contract per order; at $1 size that rounds against you. A YES bought at 7¢ must win 8% of the time to break even. | `docs/MMSELL_FEE_RECON.md`; break-even 93.9% at a 7¢ NO-sale. |
| R4 | **Any real edge accrues to the maker.** Rest a bid when the market allows it; take only when the information is about to become public. | Every taker avenue tested was efficient; maker fills bill ~0. But a resting bid is filled preferentially when it is wrong (fill 69%, missing the winners: filled −0.67¢ vs unfilled +3.77¢). |
| R5 | **Edges are cell-concentrated; a pooled read lies.** | mmsell3 read "weak" pooled and was one real cell diluted by three −EV cells; `weather_concity` (slicing a loser by its own best cities) was 2.4× worse. |
| R6 | **Small-n is a mirage until it isn't.** No claim of an edge before ~30 settled picks in a class; no re-sizing on a streak. | mmsell5: 100% win at n=17→27, then 85% and −6.2¢; five false-positive classes in the arb scanner alone. |
| R7 | **Sports reaction is structurally dead.** Both venues track the game feed; there is no follower to front-run. | XGAME: PM→Kalshi 58% vs Kalshi→PM 59%. |
| R8 | **Cross-venue disagreement is not information.** | PMDIV: the more Polymarket disagreed with Kalshi on weather, the more reliably Polymarket was wrong. |
| R9 | **Lookahead is the top process risk.** The pick is written with its timestamp before the outcome is knowable, never reconstructed after. | FREEZE v1 manufactured +15.82¢; ECON-REACT v1's "promote" was survivorship. |
| R10 | **One independent unit per event.** Two picks on the same settlement print are one pick. | 66 crypto ladder markets settle on one spot print; NBA "n=171" was ~6 dates. |
| R11 | **Sizing is fixed, not remembered.** $1 first; a step up is a recorded decision at a sample floor, never a reaction to the last result. | MARKTANGLE-2 pre-registration; every stop-loss variant lost to hold-to-settlement. |
| R12 | **Hold to settlement is the default exit.** | Backtest and forward A1–A3: every stop converts small wins into realized losses; the real tail is a gap no stop catches. |

## 5. The ledger

`docs/desk/ledger.csv` — one row per pick, appended **before** the operator acts. Columns:

```text
pick_id, written_at_utc, ticker, side, limit_price_c, size_usd, order_type,
close_time_utc, settlement_source, thesis, edge_class, confidence, expected_win_pct,
market_implied_pct, placed (yes/no/partial), fill_price_c, settled_at_utc, result,
pnl_usd, postmortem_tag
```

- `edge_class` is one of: `mechanics` (rules/source knowable early), `information` (news the
  market has not absorbed), `base_rate` (repo history says the price is off), `structure`
  (ladder/overround/dependent markets), `liquidity` (resting where flow must cross).
- `confidence` is the desk's calibrated probability that the pick settles in its favour. It is
  graded: after every 20 settled picks, the mean confidence is compared with the realized win
  rate, by `edge_class`. A class whose realized rate sits below its break-even after 30 picks
  is **closed** (`docs/desk/POSTMORTEMS.md`), the same way a book is retired.
- `placed` records what the operator actually did; a pick the operator skipped still settles
  and still grades, so the desk's calibration is measured on its own judgement, not on the
  subset that got executed.

## 6. What the desk cannot do from the sandbox, and the fixes

| Constraint | Effect | Fix |
|---|---|---|
| Egress from the Claude sandbox is denied to `kalshi.com`, `api.elections.kalshi.com`, `polymarket.com`, `weather.gov`, `help.kalshi.com`; only web **search** snippets and GitHub work. | The desk cannot read a market page, an order book, a forecast or a settlement source directly. | (a) `kalshi_desk_board` on the ops runner gives the board, rules, book and tape in ~1–2 minutes per request; (b) the operator can widen the environment's network policy ("Allow network egress" → additional domains) at claude.ai/settings — that turns minutes into seconds and unlocks source reads (NWS, BLS, Fed calendars, exchange status pages). |
| Postgres is read-only through the ops channel, one statement per request. | Repo history reads are batch, not interactive. | Acceptable: base rates are computed once per class and cached in this doc's §7. |
| No fill model. The desk sees a quote, not a fill. | A resting bid may never fill; a taker pays the spread and the rounded fee. | The ledger records `limit_price_c` and `fill_price_c` separately; a skipped or unfilled pick still grades. |
| The `ops` branch is public. | No account, order id or balance ever appears in a request or result. | Picks name tickers and prices only; P&L lives in the ledger here, not in ops results. |

## 6a. What the fetch tool has verified live (2026-09-19)

| source | readable? | what it gives |
|---|---|---|
| `gasprices.aaa.com` | yes, server-rendered | today's and yesterday's national diesel average to four decimals, week/month/year-ago, the record and its date |
| `openrouter.ai/rankings` | yes, server-rendered summary | the settlement table itself: "share of text requests … in the week beginning <Mon>", by author, with week-over-week change — but only the **last complete week**. The in-progress week is loaded client-side from `/api/frontend/v1/rankings/<section>` endpoints named in the page source |
| `openrouter.ai` community snapshot (`jampongsathorn/openrouter-rankings` on GitHub) | yes | daily copies of the same page; its market-share section has been empty since at least mid-September, so it does not substitute |
| `openrouter.ai/api/frontend/v1/rankings/market-share` | yes, JSON | weekly buckets `{"x": "<week-start Monday>", "ys": {author: value}}` back to 2025-09, **including the in-progress week**. The values are token totals, not requests: for the week of 2026-09-07 they give anthropic 3.8% and openai 18.9%, while the settlement table says 2.5% and 23.6%. Query params (`?metric=`, `?type=`) are ignored; `market-share-requests`, `requests` and `authors` return "Unknown dataset". The request-share series the `KX*SHARE` markets settle on is therefore still unread mid-week; token share is a proxy, not the metric |

## 7. Base rates the desk leans on (from the repo's own settled history)

Filled as classes are researched; each line names the query or script and the date, so it can
be refreshed. Empty on opening day by design — a number written from memory is a number
nobody can check.

## 8. Decisions this model needed from the operator (opened and answered 2026-09-19)

Recorded as `DEC-017` in `docs/DECISIONS.md`. The operator confirmed 1–3 as recommended;
4 stays open (the ops runner is the board read until the sandbox allowlist is widened);
5 defaults to the daily line plus the weekly table.

1. **Execution instrument.** The desk assumes the Kalshi app, by hand. The alternative — a new
   worker path that accepts a ticker from an env var — touches the arming path and is a
   hard stop; recommendation: **do not build it**.
2. **Skipping paper.** Under `CLAUDE.md` a new experimental activity originates in Experiment
   OS. The desk sidesteps that by trading outside the worker. That is honest only if the
   desk is recorded as **outside** XOS (this document, the ledger, no `paper_trades` rows)
   rather than pretending to be a book. Recommendation: accept, and add a `DEC` entry.
3. **Risk envelope for the desk.** Proposed: $1 per pick, 3 picks/day, $3/day, step to $5 per
   pick only after 30 settled picks with realized win rate above break-even in that class.
   The worker's `MAX_DAILY_LOSS=5` does **not** see app trades; the operator's Kalshi
   balance is the only shared limit.
4. **Network policy.** Widen the sandbox allowlist or accept the ops-runner latency (§6).
5. **Cadence of the closing brief.** Daily, after picks are written, or weekly with the
   calibration table. Recommendation: a short daily line plus the weekly table.
