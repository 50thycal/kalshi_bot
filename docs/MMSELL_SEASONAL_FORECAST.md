# mmsell seasonal forward-look — what the coming markets will actually offer

**Question:** mmsell's entire settled history is **one regime**. Sept–Nov brings NFL (weekly,
Sept), MLB playoffs (Oct), NBA/NHL openings (Oct) and the **November 3 midterms**, and we have
zero settled paper trades on any of them. The World Cup collapse already showed what a regime
change does to this book — entries fell ~5× and the best cell vanished. This doc is the
forward-look that replaces "wait and find out" with a measurement.

Two ops scripts, deliberately split so a supply number and an edge number never get confused:

| command | script | answers |
|---|---|---|
| **"mmsell supply forecast"** | `scripts/mmsell_supply_forecast.py` | how MANY tradeable markets each regime will offer, in which week, settling on which date |
| **"mmsell regime backtest"** | `scripts/mmsell_regime_backtest.py` | what each regime's maker-sell trade has been WORTH, out-of-sample |
| **"mmsell history status"** | `scripts/mmsell_history_status.py` | is the settled-history capture running, and how much history do we now own that the API has already discarded |

Shared vocabulary (the mmsell10 entry filter, the regime map, the calendar helpers) lives in
`scripts/mmsell_seasonal.py` so the two outputs stay multiplicable. Unit tests:
`tests/test_mmsell_seasonal.py`.

---

## Finding 1 (confirmed) — our entire history is one summer regime

Per-series settled `mmsell10` tape, pulled 2026-08-03:

* **Date range: 2026-07-19 → 2026-08-03.** Sixteen days. That is the whole book.
* **n ≈ 310 settled trades across 64 series**, and the series list is almost entirely
  KXWC\* (World Cup goals/mentions/assists/scores/corners), MLB totals/spreads/HR/game,
  tennis (ATP/WTA/ITF), WNBA, PGA/LIV golf, KXBTCD, and a few news series (KXTRUMPSAY,
  KXFEDMENTION).
* **Zero NFL. Zero NBA/NHL regular season. Zero NCAAF/NCAAB. Zero elections.**

The losses are concentrated in a handful of series, which is worth carrying into any new
regime: KXCLUBFGAME −27.7¢/trade, KXWCATTEND −19.8¢, KXNBATEAMANNOUNCE −14.2¢, KXMLBGAME
−6.8¢, KXTRUMPSAYMONTH −6.6¢. Head-to-head game winners and attendance/announcement props are
the repeat offenders — consistent with `docs/MMSELL_VARIANTS_THESIS.md`.

**Conclusion: seasonality cannot be backtested from our own paper books at all.** That was the
premise of this work, and it holds.

---

## Finding 2 (new, and it reshapes the plan) — the 70-day retention wall

The plan assumed `kalshi_flb.fetch_settled_for_series` could pull "Kalshi's own public history
for any series", making last season's NFL/NBA/NHL and the 2024 election ladders available
out-of-sample. **It cannot. Kalshi serves only a rolling ~70-day settled window.**

Measured by walking each series' settled feed to **cursor exhaustion** (not a page cap):

| series | status | n | pages | exhausted | oldest close |
|---|---|---|---|---|---|
| KXNHLGAME | settled | 22 | 1 | yes | 2026-05-25 (**70d**) |
| KXNBAGAME | settled | 20 | 1 | yes | 2026-05-25 (**70d**) |
| KXMLBGAME | settled | 1798 | 2 | yes | 2026-05-25 (**71d**) |
| **KXNFLGAME** | settled | **0** | 1 | yes | — |
| KXPRESPARTY | settled | **0** | 1 | yes | — |
| any of the above | finalized / closed | 0 | — | yes | — |

The tell is that series with wildly different sizes (22 rows vs 1798 rows) bottom out on the
**same date**, with the cursor exhausted. That is a retention boundary, not a budget limit.
Corroborating checks:

* `min_close_ts` / `max_close_ts` are accepted but return **zero** rows for any window older
  than the wall — the filter cannot reach behind it either.
* `status=finalized` and `status=closed` return nothing at all.
* **Authentication does not help.** Our own `backfill_weather_markets` table spans 119 days
  (2026-04-06 → 2026-08-02) — but it was first fetched on 2026-06-11 and reached back only
  ~66 days from then. It spans more than 70 days *because it has been accumulating for 53
  days*, not because the API served more.

**Consequences:**

1. **The historical regime backtest on last season's NFL is impossible** — there is no NFL
   settled data at any page budget. Same for NCAAF, NCAAB and the 2024 election ladders.
2. **A prior-year seasonal cadence cannot be computed.** The supply forecast does not print one;
   it prints the trailing cadence inside the wall and states the wall explicitly. (An earlier
   draft printed prior-year zeros, which read as "no supply" when it meant "no data" — the
   forecast now blanks uncovered cells with `?` rather than fabricating a zero.)
3. **We are losing history permanently, every day.** See "The durable fix" below.

What the wall still leaves measurable: regimes that were in season within the last 70 days.
NBA and NHL **playoffs** (May 25 – Jun 15) are retained, which makes two of the six unseen
regimes partially measurable today, and MLB is fully retained as a control. That turned out to
be enough for a first real read (n=171 entries) — see Reading 2.

---

## Reading 1 — supply (`mmsell supply forecast`, 2026-08-03)

**Live supply is thin.** Across **72,508 open markets in 3,094 series**, only **33** clear the
mmsell10 filter right now (17,412 are inside the 14-day window; pooled band rate **0.2%**).

| regime | open | in-window | eligible now | band rate | cheap-but-far |
|---|---|---|---|---|---|
| Other | 35,167 | 9,911 | 13 | 0.1% | 182 |
| Crypto | 3,137 | 1,004 | 7 | 0.7% | 10 |
| Soccer | 3,076 | 1,854 | 4 | 0.2% | 7 |
| MLB | 3,241 | 1,432 | 4 | 0.3% | 2 |
| Econ | 2,032 | 543 | 3 | 0.6% | 41 |
| Politics | 650 | 65 | 1 | 1.5% | 13 |
| **NFL** | **4,408** | 84 | **0** | 0.0% | 13 |
| **Elections** | **8,257** | 9 | **0** | 0.0% | 28 |
| **NBA / NHL / NCAAF** | 1,595 / 92 / 2,491 | 0 / 0 / 0 | 0 | n/a | 2 / 2 / 14 |

This is the seasonal setup stated numerically: **NFL already has 4,408 open markets and
Elections 8,257, but essentially none are inside the 14-day window yet.** They are supply that
has not arrived. `htcmax=336h` is what holds them out, exactly as expected.

**The window-entry calendar** (already-listed markets bucketed by `close − 14d`, i.e. the week
they become tradeable) is the assumption-free forward look. Reading the CHEAP column:

* **Aug** — 83 cheap markets already in-window, then a sharp drop (1, 13, 2, 1 per week). The
  summer book is thinning out well before the NFL supply arrives.
* **Sept** — 1, 7, 6, 0. Still thin from already-listed supply.
* **Oct** — 2, 4, **10** (week of Oct 19), 3. The Oct-19 bump is 8 Econ + MLB.
* **Nov/Dec** — 0, 0, 1, 1, 0, then 11 in the week of Dec 7.

**Caveat that matters:** the calendar can only see markets Kalshi has already listed. NFL game
markets for week 5 do not exist yet, so the Sept/Oct rows are a **lower bound**, and the wall
means we cannot supplement them with last year's counts. Re-run the forecast weekly; the Sept
and Oct rows will fill in as Kalshi lists them.

---

## Reading 2 — edge (`mmsell regime backtest`, 2026-08-03)

Replaying the mmsell10 entry (sell YES at the ask ≤7¢, mid in 5–10¢, htc 1–336h) over the
retained window, hourly candles across the 14 days before close:

4,254 markets sampled across 7 regimes, 5 series each:

| regime | markets candled | ever cheap | entries | **YIELD** | med htc | med entry |
|---|---|---|---|---|---|---|
| OtherSport | 69 | 12 | 12 | **17.4%** | 101.0h | 6.0¢ |
| NBA | 798 | 94 | 86 | **10.8%** | 22.4h | 6.0¢ |
| Tennis | 315 | 15 | 15 | **4.8%** | 192.0h | 6.0¢ |
| NHL | 704 | 23 | 21 | **3.0%** | 10.4h | 6.0¢ |
| MLB | 1,811 | 54 | 36 | **2.0%** | 1.9h | 6.0¢ |
| Crypto | 557 | 1 | 1 | **0.2%** | 9.0h | 6.0¢ |
| Elections | 10 | 0 | 0 | **0.0%** | — | — |

| regime | n | win% | mean ¢/trade | p5 | total |
|---|---|---|---|---|---|
| **NBA** | 86 | **98.8%** | **+3.84** | +5.0 | +$3.30 |
| MLB | 36 | 97.2% | +2.22 | +5.0 | +$0.80 |
| NHL | 21 | 95.2% | +0.24 | +5.0 | +$0.05 |
| Tennis | 15 | 93.3% | −1.67 | −95.0 | −$0.25 |
| OtherSport | 12 | 91.7% | −3.25 | −95.3 | −$0.39 |
| **POOLED** | **171** | **97.1%** | **+2.08** | +5.0 | +$3.56 |

**The headline: NBA is the strongest regime measured, at +3.84¢/trade on 98.8% wins with a
10.8% yield** — both a better edge and 5× the entry rate of MLB. If it survives, the winter
regime is not the supply collapse the World Cup ending implied; it is an *upgrade*.

**Three caveats that stop this being actionable yet**, in order of severity:

1. **Effective n is far smaller than 171.** §4b shows NBA's 86 entries fall on just **6
   settlement dates, averaging 14.2 positions per date**. Markets settling on one date share a
   driver, so NBA is closer to **n≈6 independent observations** than 86. Same for MLB (6 dates)
   and NHL (8). This is the single biggest reason not to size on these numbers.
2. **NBA/NHL data is playoffs-only** — the retention wall leaves only May 25 – Jun 15. Playoff
   markets are more liquid, more heavily traded and pricier than a Tuesday-night regular-season
   game. H3 is explicitly the regular-season test.
3. **Paper fill assumption.** These use the same "a resting order always fills" convention as
   the paper books, so the maker adverse-selection haircut (`docs/MMSELL_FILL_MODEL.md`) applies
   on top. Compare to our *paper* books, never to live.

Two structural readings worth carrying forward, because they rest on the market-count sample
(hundreds to thousands) rather than the trade sample:

* **Elections yielded 0 entries on 10 candled markets, median cheapest mid 27¢.** Ladder rungs
  mostly never reach the 5–10¢ band — the cheap tails sit below 5¢ or well above the cap. If it
  holds, the November concentration risk is **smaller than feared**, because the book will not
  find many eligible entries there. This is the most useful new fact for sizing November, and
  n=10 is thin enough that it must be re-measured when real midterm ladders enter the window in
  late October (gate H5).
* **Crypto yielded 0.2% on 557 markets.** The BTC/ETH tail that carries 44 of our settled
  mmsell10 trades barely converts in the *retained* window — consistent with
  `docs/MMSELL_CRYPTO_STUDY.md`'s finding that the measurable crypto window (`htc<1h`) is not
  the window mmsell trades. Flagged UNMEASURABLE by the coverage gate rather than scored.

### H7 (anchor stops) — first out-of-sample read

| regime | hold mean | hold p5 | Δmean @12/20/30 | Δp5 @12/20/30 | %exit @12 |
|---|---|---|---|---|---|
| NBA | +3.84 | +5.0 | −3.47 / −2.10 / −2.10 | −33 / −36 / −36 | 13% |
| NHL | +0.24 | +5.0 | −4.90 / −2.29 / **+0.00** | −50 / −50 / **+0** | 24% |
| MLB | +2.22 | +5.0 | −0.17 / **+0.28** / **+0.28** | −16 / **+0** / **+0** | 6% |
| Tennis | −1.67 | −95.0 | −0.87 / −2.87 / −0.53 | **+79 / +64 / +63** | 47% |
| OtherSport | −3.25 | −95.3 | **+4.67 / +3.75 / +2.67** | **+85 / +70 / +66** | 25% |

Coherent with the jump-vs-continuous thesis, and it sharpens it: **the stop only helps where
hold actually has a tail.** NBA/NHL/MLB have p5 = +5.0¢ (no loss inside the 5th percentile in
this sample), so a stop can only subtract. Tennis and OtherSport — the two regimes that *did*
take losses — are the two where the stop lifts the tail sharply (+63 to +85¢) and OtherSport is
the only regime where it improves mean **and** tail together, passing the anchor gate. Small n,
but it says the stop is a **tail-regime tool**, not a universal one.

### H6 (correlated settlement) — measured, and the answer is "not in sports"

| regime | dates | avg/date | max/date | loss% | **overdispersion** | worst day |
|---|---|---|---|---|---|---|
| NBA | 6 | 14.2 | 18 | 1.2% | **0.89×** | −$0.35 |
| MLB | 6 | 4.7 | 9 | 2.8% | **0.18×** | +$0.00 |
| NHL | 8 | 2.5 | 6 | 4.8% | **0.63×** | −$0.70 |
| Tennis | 5 | 2.2 | 3 | 6.7% | **1.23×** | −$0.90 |

All at or **below** 1.0× — losses on a shared settlement date are not clustering in sports, even
with NBA averaging 14.2 positions per date. That is the "many small independent positions"
assumption holding up where it was measurable. It is **not** evidence about elections, which is
the case the risk model actually fears: zero election entries means H6 is untested exactly where
it matters. Re-run in late October.

---

## Reading 3 — settlement-date concentration (the risk to size before November)

Cheap already-listed markets grouped by settlement date, against the position caps
(`mmsell_max_open_positions=200` paper, `mmsell_live_max_open_positions=60` live):

| close date | cheap | % paper cap | % live cap | top regimes | flag |
|---|---|---|---|---|---|
| 2026-08-07 | 33 | 16% | **55%** | Other 13, MLB 13, Crypto 3 | |
| 2026-08-06 | 14 | 7% | 23% | MLB 9, Soccer 4 | |
| 2026-08-05 | 12 | 6% | 20% | Other 10, MLB 2 | |
| **2026-11-03** | 11 | 6% | 18% | Econ 8, Other 2, Elections 1 | **CORRELATED** |

Two things stand out. **Election day already shows up** even at 3 months out, and today's
concentration is *not* an election problem — **55% of the live cap already settles on a single
August date**. The concentration risk is real and present, not hypothetical and November-only.

**Ladder structure** (the partial natural hedge): the Elections regime lists 40+ series with
2–23 rungs per event — KXGOVCA 23, KXGOVSENDIFF 15, KXGOVMINOMR 15, KXSENATEFLR 10. Within one
event the rungs are mutually exclusive, so **at most one cheap tail per event can lose**. Across
events that protection vanishes: a national swing moves every race the same way at once.

**Therefore: any settlement-date cap must count EVENTS, not markets.** Ten rungs of one
governor's race is one bet; ten rungs across ten races is ten correlated bets.

### The cap — BUILT

Both halves of the recommendation above are now enforced at entry time, per book, in
`MmSellTracker.run_once` (`_settlement_cap_blocks`):

* **Settlement-date cap.** A book refuses a new entry once `>= mmsell_settlement_cap_pct`
  (default 25%) of its OWN `max_open_positions` already settle on that candidate's date — 15
  positions at `mmsell_live_max_open_positions=60`, 50 at paper's 200. Today's Aug-07 date (33
  cheap markets, 55% of the live cap) is exactly the case this catches. A twin inherits the
  tighter live-shaped number automatically, since the date cap is computed as a percentage of
  the SAME `cap` value the existing position-count cap already uses (paper's 200 vs a twin's
  live-sized 60) — no separate twin-vs-paper branch was needed.
* **Correlated-event cap.** On a `mmsell_settlement_correlated_regimes` date (default
  `"Elections"`), a NEW event is refused once `>= mmsell_settlement_event_cap` (default 5)
  distinct events are already open that date. Adding another rung to an event ALREADY
  represented stays allowed — that pairing is the mutual-exclusivity hedge itself (at most one
  rung of one race can lose), not additional correlated exposure. Regime is evaluated at
  decision time via `kalshi_bot.mmsell.regimes.regime_of`, not stamped once, so a later addition
  to the correlated-regime list takes effect immediately rather than only for new markets.

New table `mmsell_settlement_meta` (migration `d2e3f4a5b6c7`) records each candidate's close
time + event ticker the first time it is seen (insert-only, mirroring the regime-history
capture's pattern), which is what makes "how many of my OTHER open positions settle on this
date" a queryable question — `paper_positions` tracks status and strategy but never recorded
when a market closes. Both knobs are config-gated (`mmsell_settlement_cap_enabled`, on by
default) and fail open on a read error, matching every other soft gate in this tracker (the vol
gate, the anchor stop) — a broken risk check must never be what stops the book trading.

Tests: `tests/test_mmsell_settlement_cap.py` — the per-book isolation, the twin-sized cap, the
event-vs-market distinction, the own-ticker exclusion, and the calendar-date boundary.

---

## Pre-registered per-regime hypotheses (Phase 4)

Registered **before** the regimes arrive, so the results cannot be re-scoped afterwards. Each is
judged on **realizable** ¢/trade (`mmsell fill model`), not blended paper — the standing rule
from `docs/MMSELL_FILL_MODEL.md`.

| # | regime | hypothesis | gate |
|---|---|---|---|
| H1 | NFL | Game/total/spread markets yield entries at ≥3% and are **not** worse than the summer book | at n≥100 settled: KEEP if realizable ≥ 0; KILL if ≤ −1.0¢/trade |
| H2 | NFL props (TD/yards) | Prop tails behave like the summer prop book (+EV), unlike h2h winners | same gate, judged separately from H1 |
| H3 | NBA/NHL regular season | Yield ≥5% (as the playoff sample suggested) and edge ≥ summer book | at n≥100: KEEP if realizable ≥ 0 AND yield ≥3% |
| H4 | MLB playoffs | Worse than MLB regular season — fewer games, more informed flow | at n≥60: flag if realizable is ≥1.0¢ below the regular-season book |
| H5 | Elections | Yield stays **<2%**, so November never accumulates a large correlated book | re-measure in late Oct; if yield ≥5%, the concentration cap becomes a **blocker** before Nov 3 |
| H6 | correlation | Per-settlement-date loss counts are overdispersed vs binomial (factor >1.5) in at least one regime | measured by `mmsell regime backtest` §4b at n≥5 multi-position dates |
| H7 | anchor stops | The A1/A2/A3 bid stop helps on continuous-path regimes and hurts on jump regimes (games) | §4a: Δp5 up AND Δmean ≥ −0.3¢ |

**Named killers.** H1/H3 fail if the new regimes' h2h winners reproduce the World Cup pattern
(81.7% win, −9.9¢/trade). H5 fails if the midterm ladders list cheap rungs at scale in late
October. Any of these firing means the book should **shrink** into the new regime, not follow it.

---

## The durable fix — capture settled history as it happens

The retention wall is not going away, and every day we do not capture is a day of history
permanently lost. **The repo already has the exact pattern**: `kalshi_bot/weather/backfill.py`
enumerates settled markets per series into `backfill_weather_markets` / `backfill_weather_candles`,
on the Railway worker (the only place with Kalshi credentials and a read-write DB), as a bounded
chunk per cycle. It has accumulated 9,984 markets and 364,184 candles this way.

**BUILT** — `kalshi_bot/mmsell/history.py` (`RegimeHistoryCapture`), riding along wherever the
mmsell book already runs (weather and live cycles), writing into `backfill_regime_markets` /
`backfill_regime_candles`:

1. every `MMSELL_HISTORY_ENUMERATE_MINUTES` (default 6h), enumerate settled markets for each
   configured series and **insert** the unseen ones — insert-only, because re-enumeration
   overlaps by design and rewriting a row would reset its `candles_fetched` flag and re-fetch
   the same candles forever;
2. every cycle, take up to `MMSELL_HISTORY_MARKETS_PER_CYCLE` (default 30) markets still lacking
   candles, **oldest first**, and store their path over the final `MMSELL_HISTORY_CAPTURE_HOURS`
   (default 336h = mmsell's whole holding window) at hourly granularity.

**Oldest-first is load-bearing, and it is the opposite of what the first version did.** The first
real run made the difference concrete: enumeration queued 11,361 markets, of which **9,986 were
MLB** — a series that settles daily and sits comfortably inside the retention window — while
**1,361 were NBA/NHL markets from a season that has ended** and will never produce another row.
Newest-first put ~10 hours of replaceable MLB work ahead of the irreplaceable set while it aged
toward the wall. The original rationale ("a fresh market is likeliest to still have a fetchable
path") confused *most-likely-to-succeed* with *most-valuable-to-attempt*: a market that settled
today will still be fetchable tomorrow; one from two months ago may not be.

A market whose candles Kalshi no longer serves is marked done at **zero rows** rather than left
pending — which is exactly what makes oldest-first safe, since an expired block sits at the head
of the queue and would otherwise wedge everything behind it. A 404 on the live candle endpoint
falls back to the historical endpoint — the path that matters more as a market ages toward the
wall.

The one structural difference from `weather/backfill.py`: that job reaches back once and latches
`_enumerated`. This one can never finish, because the window it reads keeps sliding away.

**Default series** (`MMSELL_HISTORY_SERIES`, env-overridable on Railway without a redeploy) lead
with the regimes we have zero paper history for, since those are the ones that cannot be
reconstructed later: NFL/NCAAF game+total+spread, NBA/NHL, NCAAB, MLB (the control — we have
paper history there, so it validates the captured data), and the midterm election series.
Deliberately explicit rather than prefix-discovered: Kalshi lists 3,000+ sports series, mostly
per-team spin-offs of one driver, and enumerating them all would spend the whole API budget on
noise. Use `--list-series` to find real tickers before adding one.

**Check it is working** with `{"type":"script","name":"mmsell_history_status"}`, which leads with
freshness (a stale write or a pending queue that only grows means the capture is failing quietly)
and reports the **BEYOND-WALL** count — markets we hold whose close is already older than
Kalshi's ~70-day window. That column is the whole point of the job, and it should only ever grow.

`mmsell_regime_backtest` still reads the live API, so it stays bounded to the rolling window
until the captured tables have enough depth to replace it — **re-run it monthly**, because its
coverage moves with the calendar.

---

## Running them

```jsonc
{"type":"script","name":"mmsell_supply_forecast","args":["--weeks","18"],"id":"fc-1"}
{"type":"script","name":"mmsell_supply_forecast","args":["--probe"],"id":"fc-probe"}          // API shapes + the retention probe
{"type":"script","name":"mmsell_supply_forecast","args":["--list-series","NFL,Elections"]}    // real tickers, for SEED_SERIES
{"type":"script","name":"mmsell_regime_backtest","args":["--regimes","NBA,NHL,MLB"],"id":"rb-1"}
{"type":"script","name":"mmsell_regime_backtest","args":["--interval","1","--sample","60"]}   // fine 1-min pass
{"type":"script","name":"mmsell_history_status","id":"hist-1"}                               // is the capture accumulating?
```

**Traps these scripts encode** (each cost a wrong answer during development):

* **A truncated open-market scan silently understates every supply count.** `--event-pages`
  now warns when the cursor is still live at the cap.
* **An unreached week is not an empty week.** Cadence cells outside a regime's retained window
  print `?`, never `0`.
* **Seed series must be exempt from the per-regime cap.** NFL has 4,400+ open markets across
  dozens of side-series; ranking "currently trading" first filled the entire NFL budget with
  short-tickered futures and left `KXNFLGAME` unpulled — which read as "NFL has no history".
* **The entry price is the yes ASK, not the mid.** mmsell rests a BUY-NO at the no-bid, which
  sells YES at `100 − no_bid == yes_ask`. Pricing the mid understates every entry by half a
  spread — a large share of the whole edge at 5–7¢.
* **`KXSENATE` / `KXHOUSE` / `KXGOV` / `KXMIDTERM` do not exist** as series tickers. Use
  `--list-series` before adding a seed.
* **`KXNCAABB*` is NCAA *baseball*,** not basketball — the prefix would put a spring sport in a
  winter regime.
