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
| `kalshi_desk_board --ticker` order book | yes, verified live 2026-09-19 20:31Z | the `orderbook_fp` branch works: `KX30YMORTW-26SEP24-T7.01` printed 8 resting levels a side (YES 40c x94, 36c x600, then a wall of penny bids; NO 59c x60, 37c x2, …). Every earlier empty-book read was the retired key, not an empty market. The desk can now see depth, which is what R4's "rest a bid" needs |
| `openrouter.ai/api/frontend/v1/rankings/market-share` | yes, JSON | weekly buckets `{"x": "<week-start Monday>", "ys": {author: value}}` back to 2025-09, **including the in-progress week**, updated near real time (two reads 11.7h apart on 2026-09-19 moved the week-of-9/14 bucket by 7.1T). The values are **absolute tokens per author** (`others` is the remainder, so the sum is the platform total): the week of 2026-09-07 sums to 126.8T, the figure the public token-usage trackers publish for that week. They are not the request-share metric the `KX*SHARE` markets settle on (for 2026-09-07 tokens say anthropic 3.8% / openai 18.9%, the settlement table says 2.5% / 23.6%). Query params (`?metric=`, `?type=`) are ignored; `market-share-requests`, `requests` and `authors` return "Unknown dataset". So: the endpoint gives the `KXTOKENUSE` week-to-date total directly, and only a proxy for share |

## 7. Base rates the desk leans on (from the repo's own settled history and the settlement sources)

Filled as classes are researched; each line names the source and the date, so it can be
refreshed. A number written from memory is a number nobody can check, so none are.

| class / series | what the desk verified | source, date |
|---|---|---|
| `KXDIESELW` (EIA weekly on-highway diesel, Monday survey) | EIA has printed **5.5–6.7¢ above** AAA's same-Monday daily average the last two weeks (6.285 vs 6.23 on 9/14; 5.967 vs ~5.90 on 9/7). AAA daily 9/14→9/19: 6.23, 6.31, 6.40, 6.4476, **6.4866** (Sat 9/19, record). | `desk_fetch gasprices.aaa.com`, EIA release pages, 2026-09-18/19 |
| `KX30YMORTW` (Freddie Mac PMMS, Thursday) | PMMS is the Thu–Wed window mean of application rates, and it has matched the daily Optimal Blue / Mortgage Daily 30-yr series to **within 1bp**: 9/17 print 6.95 vs mean(9/10 6.88, 9/11 6.95, 9/14–15 6.95, 9/16 7.05) = 6.96. MND's top-tier index runs ~15–25bp above PMMS in a rising week (7.19–7.24 on 9/16–9/18 vs 6.95). So by Friday two of the five window days are known, and the market ladder can be checked against them. | web search (mortgagedaily.com, mortgagenewsdaily.com, freddiemac.com), 2026-09-19 |
| `KXSPRLVL` (EIA WPSR Table 1, Wednesday) | Weekly SPR changes during the 172M-barrel IEA release: −3.4/wk (8/14→8/28), −1.2 (9/4), −0.4 (9/11); draws are decelerating as the release winds down. The 1M-barrel strike spacing is the same size as the weekly noise, so a strike sits inside the noise unless DOE's delivery schedule is known. | web search (EIA WPSR summaries), 2026-09-19 |
| `KXSOFRD` (NY Fed SOFR, next business day 08:00 ET) | Markets close before the print. The day after the 9/16 hike SOFR set at 3.85 (IORB−5). The Friday ladder's yes asks summed to 177¢ across seven bins: nothing is takeable, and the desk has no read on day-two drift. | ops event view + web search, 2026-09-19 |
| `KXTOKENUSE` (OpenRouter weekly tokens) | Week totals: 126.2T (8/31), **126.8T (9/07)**. Week-to-date is readable from the market-share endpoint (§6a) by summing the bucket (the author list rotates — `qwen` left and `meta` entered between two Saturday reads — but `others` absorbs the remainder, so the sum stays the platform total). Week of 9/14 measured: **96.45T at Sat 04:08Z, 103.27T at 13:43Z, 108.99T at 20:31Z** → 0.711 T/h then 0.841 T/h, against a 0.777 T/h average for the week to date. The weekend is **not** materially slower than the weekday pace, which contradicts the first guess. 27.5h remain at the last read; at 0.65–0.84 T/h the week lands **126–132T**. |
| `KX*SHARE` (OpenRouter request share by author) | Settlement metric unreadable mid-week; token share is a proxy the market already tracks (OpenAI tokens 18.9%→13.7% week over week and the market moved from 23.6 to ~17.5). No desk edge without the request series. | §6a, 2026-09-19 |

## 8. Decisions this model needed from the operator (opened and answered 2026-09-19)

Recorded as `DEC-017` in `docs/DECISIONS.md`. The operator confirmed 1–3 as recommended
(1 with "automate later", parked); 4 is approved — the operator widens the allowlist in the
claude.ai environment settings, and the ops runner stays the board read until a session starts
under the wider policy; 5 defaults to the daily line plus the weekly table.

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

## 9. Handoff — where the desk stands (rewrite this at the close of every session)

**As of 2026-09-19 ~20:45 UTC (Sat).** Role playbook: `.claude/sessions/discretionary-desk.md`.
Any session — or any model that can read this repo and push to GitHub — continues from this
section, the ledger and the postmortems; nothing else was needed to get here.

**Open positions ($2 at risk, both placed by the operator in the app):**

| pick | market | side / fill | settles | grade with |
|---|---|---|---|---|
| D-2026-09-19-001 | `KXDIESELW-26SEP21-T6.52` (EIA weekly diesel > $6.52) | YES @ ~76c | Mon 2026-09-21 ~17:00 ET | EIA weekly on-highway diesel, U.S. average (`desk_fetch https://www.eia.gov/petroleum/gasdiesel/`, or web search). AAA was $6.4866 on Sat 9/19; EIA has printed 5.5–6.7c above AAA's same-Monday figure. |
| D-2026-09-19-002 | `KX30YMORTW-26SEP24-T7.01` (Freddie Mac PMMS > 7.01%) | YES @ ~43c | Thu 2026-09-24 12:00 ET | Freddie Mac PMMS first published value (`freddiemac.com/pmms`, or web search). Window Thu 9/17–Wed 9/23; 9/17 = 7.01 and 9/18 = 7.05 on the Optimal Blue/Mortgage Daily series that tracks PMMS within 1bp. |

**Windows the last session identified but has not traded:**

- `KXTOKENUSE-26SEP21` (OpenRouter tokens Sep 14–20): the week ends Mon 00:00 UTC and the ladder
  trades until Mon 03:59 UTC. **Saturday 20:31Z read: 108.99T week-to-date, pace 0.84 T/h**, so the
  week projects to 126–132T — a band that straddles the >126, >128 and >130 strikes, and no pick was
  taken (`D-2026-09-19-NP1`). The **Sunday ~20:00 UTC** read is the one that matters: ~4h unread,
  the ladder still open, and the projection tight enough to price a strike.
- `KX*SHARE` (OpenRouter request share): still no mid-week read of the settlement metric. Pass.

**Scheduled check-ins are bound to the session that created them** (Claude Code routines):
"Desk: daily board read and picks" (13:30 UTC daily), "Desk: Saturday OpenRouter pace sample"
(Sat 20:30 UTC), "Desk: Sunday-evening OpenRouter token pick" (Sun 20:00 UTC). A new session
re-creates what it needs (playbook, Startup Routine step 3); the operator can also run any step
by asking.

**Sandbox limits still in force:** Kalshi, EIA, Freddie Mac, NY Fed, Mortgage News Daily and most
data sites are blocked from the sandbox; use the ops runner (`kalshi_desk_board`, `desk_fetch`) and
web search. The operator approved widening the environment's network allowlist (DEC-017); until
that is done, budget minutes per read.

**Lessons so far (n=0 settled — nothing is a pattern yet):** the operator paid 76c on D-001
against a 55c cap, so the report must carry the cap in the first line; the board's thin books move
20 points between two reads minutes apart; and a projection band that straddles three strikes is a
no-pick, logged as one, not a reason to take the nearest strike.

