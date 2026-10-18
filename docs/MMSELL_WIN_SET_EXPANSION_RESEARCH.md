# WIN-set expansion research — more trades for `Nmmsell10` (2026-10-17)

**Role:** Research Lab, read-only. Nothing here changes a book, a gate, a Version or live exposure.
**Asked by the owner** (2026-10-17): can the WIN-set canary trade more, by adding series from
the market catalog, and what would improve it. Every number is from an ops read named below,
gross of fees unless it says live, and holds-to-settlement at the maker price.
**Context:** `docs/MMSELL_WIN_SET_CANARY.md` (the book), `docs/MMSELL_LIVE_CANARY_PATTERNS.md`
(why WIN), `docs/MMSELL_LOOKALIKE_PROBE.md` (the replay tool and its gate).

The catalog service itself is not reachable from the ops channel (SQLite on its own volume,
bearer token, no ops route). Its inputs are: public Kalshi rules and listings
(`catalog_review_sources`, `mmsell_lookalike_probe`) and the bot's own Postgres. Those are what
was read.

## Bottom line

| question | answer |
|---|---|
| Add commodities (gold, natgas, Brent, copper, WTI, AAA gas)? | **No.** Clean on paper, losing live; the live fills land on the losers (§1). |
| Add new mention / approval series? | **KXTRUMPMENTION yes, first.** Others positive but thinner (§2). |
| Lift the rain 3-city cap? | Small gain (~1 extra rain market a day), winter risk; **timing is the better rain lever** (§3). |
| Widen to YES 8–10¢? | Not across the board. **Index above/below only is plausible**, with thin live evidence (§4). |
| Smaller / thinner markets? | Already tested and retired (Aug). The WIN series are not being cut by the scanner (§5). |
| New lead | **Fast fills lose.** On the WIN series every settled live loser filled within 30 min of placement (§6). |

## 1. Commodities — closed for this entry

Paper markets (YES 5–10¢, since 2026-08-20) split by what live did on the same market
(`nm-adverse-1`):

| group | live on that market | markets | losing | paper ¢/ct |
|---|---|---|---|---|
| commodities | never tried | 316 | 6.6% | +0.6 |
| | tried, not filled | 24 | **0%** | +6.7 |
| | **filled** | 77 | **9.1%** | **−2.6** |
| current 7 WIN series | never tried | 402 | 2.7% | +4.9 |
| | tried, not filled | 49 | **0%** | +6.5 |
| | **filled** | 97 | 5.2% | +1.5 |

- The fill *is* the selection: unfilled orders sat on markets that all won, filled ones lose
  more. That is the maker adverse selection of `docs/MMSELL_FILL_MODEL.md`, now visible per
  market.
- Commodities start near break-even on paper once oil and gasoline are counted; the clean
  gold/natgas tape was the favourable slice.
- Every live commodity loser was also a paper loser (`nm-commod-1`) — not a data artefact.
- Timing does not rescue it (`nm-r4-commod-time-1`): losing fills spread from 3h to 24h
  before close; metals+natgas+Brent (7 losers in 47 fills) are no better than oil+gasoline
  (12 in 104).
- Per `docs/MMSELL_SCAN_DEPTH.md`'s revival rule, a maker-fill problem is reopened only by a
  mechanically different entry, not by more paper.

## 2. New series — replay on every listed market

`mmsell_lookalike_probe`, first in-cell entry (YES mid 5–10¢, ask ≤ 7¢), up to 1,500 settled
markets per series, KXRAIN control **reproduced** (`nm-r2-cands-2`; an earlier 300-market run,
`nm-r2-cands-1`, did not reproduce its control and is void):

| series | what it is | entries | days | losses | ¢/ct | t | months |
|---|---|---|---|---|---|---|---|
| **KXTRUMPMENTION** | Trump says a word at a named event | 53 | 43 | 0 | **+6.64** | 13.4 | 2025-10..2026-10, none negative |
| KXAPRPOTUS | RCP presidential approval, bucket | 103 | 59 | 3 | +3.67 | 2.35 | 2024-11.. |
| KXSECPRESSMENTION | Press Secretary says a word | 52 | 19 | 1 | +4.73 | 1.52 | Jul 2026 −13.4 |
| KXBERNIEMENTION | Bernie Sanders says a word | 30 | 15 | 1 | +3.43 | 0.93 | May 2026 −3.3 |
| KXWORLDNEWSMENTION | anchor says a word | 8 | 8 | 0 | +6.88 | — | thin |
| KXMAMDANIMENTION | Mamdani says a word | 7 | 6 | 0 | +6.86 | — | thin |
| KXTRUMPAPPROVE | approval, above/below | 90 | 32 | 4 | +2.18 | 1.10 | Oct +0.1 (`cands-1`) |
| KXNASDAQ100 | Nasdaq closing range | 22 | 9 | 2 | −2.27 | — | 38% tradeable ≥1h — drop |
| KXRAINWKND | weekend rain | 6 | 3 | 0 | +6.67 | — | thin |

None passes the pre-registered gate yet (HOLD = under 100 entries / 30 days, or months not
counted); all are tradeable under the 1h entry floor except KXNASDAQ100.

**Rules** (`nm-r2-rules-1`):
- **KXAPRPOTUS** settles on the RealClearPolitics average at 11:00 ET; a bucket ladder whose
  event Kalshi marks `mutually_exclusive: true` (the rung cap does not apply).
- **The mention series** share KXTRUMPSAY's structure: YES if the exact word (or plural /
  possessive) is said during one named appearance, settled on video then transcripts.
  `can_close_early: true`; events are not mutually exclusive, so the rung cap holds them to 3
  words per appearance.

**Live:** none of these is in `registry/series_manifest.json` or the `SERIES_TYPES` taxonomy,
so live refuses them today; paper books already trade them (paper since 08-11: KXTRUMPMENTION
33 markets / 0 losers, KXAPRPOTUS 28 / 0). KXAPRPOTUS has 2 live fills from an older book, both
winners.

**Supply** (`nm-r5-scan-1`, last 30 days of scanner candidates): KXTRUMPMENTION reached the
cell on 38 markets (~1.3/day) — the largest addition on the list; KXAPRPOTUS 5; others 0–2.

**Path to live, if chosen:** read rules (done for samples) → `SERIES_TYPES` entry (Platform
Change Review) → manifest PR graduating the series (**also admits it to `Kmmsell10`**, which
trades every graduated series — a real-money change for that book) → a new `mmsell-win-set`
Version adding it to `onlyx`. Suggested order: KXTRUMPMENTION alone first.

## 3. Rain — the 3-city cap and entry timing

**Where the cap comes from.** It is not a rain rule. `mmsell_event_rung_cap=3`
(`config.py`) limits open rungs on any event Kalshi marks *not* mutually exclusive. It was
built for nested threshold ladders (one WTI print settles every rung). A KXRAIN event (one date,
many cities) is flagged non-exclusive, so it inherits the cap although cities are separate bets.

**What lifting it buys.** The full replay found 3.3–4.1 in-cell rain markets a day (272 and 337
entries over 83 days in the two runs), against a cap of 3 — roughly +10–35% more rain
trades, concentrated on busy days. Earlier: the books' union saw 3.6 a day, max 10
(`docs/KXRAIN_MMSELL_CELL.md` §3.3). Losses were spread (no front hit several cities), but the
edge has faded (`nm-r3-rain-replay-1`): Jul–Aug +6.5¢, Sep–Oct +3.3¢; winter is the open risk
and the case where a higher cap hurts.

**Timing** (`nm-r3-rain-replay-1`, 337 entries / 83 days, gate PROMOTE):

| entry window | entries | losses | ¢/ct | vs current rule |
|---|---|---|---|---|
| current (first in-cell) | 337 | 5 | +5.05 | — |
| early (24–48h) | 90 | 1 | +5.31 | SAME |
| mid (6–24h) | 183 | **4** | +4.36 | SAME |
| **late (1–6h)** | 107 | 1 | **+5.66** | +1.13¢, t 1.88 — just under the pre-registered t ≥ 2 |

The mid window holds 4 of 5 losses, consistent with §3.5 of the rain doc. The bot's own tape
has too few rain losers (2 paper, 2 live — `nm-r3-rain-time-1`) to decide it.

## 4. YES 8–10¢ — per series

Paper since 08-11 and live fills from older books (`nm-band-2`, `nm-r1-idx-adv-1`):

| series | 5–7¢ paper | 8–10¢ paper | 8–10¢ live filled |
|---|---|---|---|
| **KXINXU** | 108 / 2 losers / +4.6¢ | **79 / 2 / +6.1¢** | 6 markets, 1 loser |
| **KXNASDAQ100U** | 69 / 1 / +5.3¢ | **74 / 2 / +6.1¢** | 3, 0 |
| KXRAIN | 224 / 2 / +5.6¢ | 167 / 9 / +3.4¢ | 25, 1 |
| KXTRUMPSAY | 58 / 2 / +3.0¢ | 31 / 2 / +2.1¢ | 5, 1 |
| KXINX | 29 / 1 / +3.0¢ | 19 / 2 / **−1.8¢** | 2, 0 |

- Only the index above/below pair holds up at 8–10¢ on paper (~150 more markets).
- Live at 8–10¢ is 39 filled markets with 3 losers (7.7%) — close to the ~9% break-even at that
  price. Too thin to support a widening; the full-history replay could not help (only 7–11
  entries from 900 sampled index markets, `nm-r1-idx810-1`).
- A widening would need a 1-lot live arm on a fresh tag with its twin; paper cannot measure the
  fill selection that decides it (§1).

## 5. Smaller markets / scanner coverage

- **Tested and retired** 2026-08-14 (`docs/MMSELL_SCAN_DEPTH.md`): scanning past the top 150
  events lost ~1¢ a trade and lost total dollars.
- **The WIN series are not being cut** (`nm-r5-scan-1`, `nm-r5-fed-1`): KXINXU, KXNASDAQ100U,
  KXRAIN, KXTRUMPSAY and KXTRUMPMENTION all reach the candidate stage. KXFEDMENTION shows none
  in the last 30 days only because its markets trade around each FOMC meeting (seen the weeks of
  Jul 27, Sep 7 and Sep 14); the Oct 28 meeting's markets have not yet made the volume cut.
- Nothing to build here.

## 6. New lead — fast fills lose (post hoc)

Settled live mmsell fills, time from placement to fill (`nm-r6-wait-1`):

| series | < 5 min | 5–30 min | 30–120 min | 2h+ |
|---|---|---|---|---|
| **WIN 7** | 35 mkts, 3 losers (8.6%) | 67, 3 (4.5%) | **55, 0** | **31, 0** |
| all other series | 1,131, 92 (8.1%) | 931, 79 (8.5%) | 453, 43 (9.5%) | 189, 13 (6.9%) |

On the WIN series all 6 settled live losers filled within 30 minutes of the order; 86 markets
that filled later lost none (chance of all six landing in the faster group by luck ≈ 2%). Index: both live losers were
near-instant fills (`nm-r6-filltime-1`). The other series do not show it, and the split was
found after looking, so it is a **lead for a forward test**, not a rule. It is distinct from
the killed flow veto and reactive cancel (`docs/MMSELL_FLOW_VETO_THESIS.md`), which read trade
flow; this reads only how fast our own resting order was taken.

### 6a. Follow-up (2026-10-18): the timing effect is a regime, the spread effect is not

Asked by the owner to dig further. Every live mmsell fill with placement context
(`execution_order_context`, recorded from September), all series.

**Fill speed** (`ff-all-agg-1`, since 08-20, 1,342 settled markets): losers 8.9% under 5 min,
7.4% at 5–30, 6.8% at 30–120, 1.7% at 2h+ — monotonic. **But not stable by month**
(`ff-month-1`):

| month | < 30 min | 30–120 min | 2h+ |
|---|---|---|---|
| Aug | 72 of 880 lost | 28 of 283 | **11 of 111 (as bad)** |
| Sep | 49 of 617 | 11 of 153 | 2 of 70 |
| Oct | 29 of 319 | 4 of 59 | 0 of 33 |

August shows no effect. This is the pattern `docs/MMSELL_REPLAY_PROBES_20261003.md` already
named — time-based levers flip between windows on this book — so fill speed is a regime
property, not a rule. A fill cannot be refused anyway; the only usable form would be a
pre-trade proxy.

**What fast fills look like at placement** (`ff-win-rows-2`, WIN series): a short queue ahead
(median 30 contracts vs 100 for slow fills), thinner bid depth (62 vs 300), and a 1¢ spread.
4 of the 6 WIN losers were at a 1¢ spread; 4 had ≤ 5 contracts ahead.

**Spread is the stable signal** (`ff-month-1`, all series):

| month | 1¢ spread | 2¢ or wider |
|---|---|---|
| Sep | 253 mkts, 7.1% lost, +0.3¢/mkt | 107, **0.9%**, +6.2¢/mkt |
| Oct | 289, 9.7% lost, −5.9¢/mkt | 122, **4.1%**, +5.6¢/mkt |

Same direction both months; queue length alone is weak (0–10 ahead 8.4% vs 100+ 6.8%). A 1¢
spread means the market is efficiently priced and heavily traded — our 7¢ is roughly the fair
price there and the fills that arrive are informed. Caveats:
- found after looking (a 1¢ vs 2¢+ split), and **overlapping the window of the pre-registered
  P-SPREAD forward test** (≥ 3¢ vs ≤ 2¢, floor 80 fills from 10-01). That test's gate is
  untouched and must be read on its own terms, not through this split;
- 2¢+ is only ~30% of fills — a filter would cut trade count about 70%, though the cut trades
  run near zero or negative;
- `cents_per_mkt` mixes clip sizes; the loss rates are the cleaner read.

**Recommendation:** pre-register "skip entries at a 1¢ spread" as its own forward test (paper
book on the same universe, plus the live twin tape), judged after P-SPREAD reads out. Do not
build a fill-speed rule.

## 7. Recommended order (each needs its own owner decision)

1. **KXTRUMPMENTION** into the WIN set — strongest replay, largest supply, same contract shape
   as KXTRUMPSAY. Hard stop: graduation also opens it to `Kmmsell10`. **Parked** in
   `docs/workstreams/ACTIVE.md` (owner, 2026-10-18: add eventually).
2. **Spread ≥ 2¢ filter** — forward test (§6a); the stable signal of this research.
3. **Rain late-entry window** — pre-register the late (1–6h) rule as a forward paper test; it
   sits at t 1.88 today.
4. **Index 8–10¢** — only as a 1-lot live arm with a twin.
5. **Do not pursue:** commodities with this entry, scanning deeper, the rain cap on its own, a
   fill-speed rule (§6a).

## Ops reads (all read-only, 2026-10-17)

`nm-series-paper-1/2`, `nm-series-live-1`, `nm-band-1/2`, `nm-commod-1`, `nm-adverse-1`,
`nm-r1-idx810-1`, `nm-r1-idx-adv-1`, `nm-r2-cands-1` (void), `nm-r2-cands-2`, `nm-r2-rules-1`,
`nm-r3-rain-time-1`, `nm-r3-rain-replay-1`, `nm-r4-commod-time-1`, `nm-r5-scan-1`, `nm-r5-fed-1`,
`nm-r6-filltime-1`, `nm-r6-wait-1`. 2026-10-18: `ff-win-rows-2`, `ff-all-agg-1`, `ff-month-1`.
