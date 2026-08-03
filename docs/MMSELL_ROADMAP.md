# mmsell roadmap — the unexplored ideas, adjudicated

*Written 2026-08-03. Every number below was measured this session over the ops channel; the
queries are reproducible from the SQL in each section. This doc exists so the verdicts and the
**pre-registered gates** are on record BEFORE anything gets built — the same discipline
`docs/MMSELL_VARIANTS_THESIS.md` used for mmsell4–11.*

**Scope:** the seven "genuinely unexplored" mmsell directions, plus two operator questions —
scaling size into near-certain winners, and locating the tail by market type.

---

## 0. Verdict summary

| # | idea | verdict | why, in one line |
|---|---|---|---|
| 1 | **Clip size / fee amortization** | **DEAD — premise is false** | Live maker fills pay **0.013¢/contract**, not 1¢. There is nothing to amortize; the 1¢ fee exists only in the paper simulator. |
| 2 | Ladder overround as entry signal | **PARK — measured, no signal** | Only 46.5% of candidate flow sits in a partition event, and candidate-bearing events are *not* more overround than the rest (104.5 vs 105.5). |
| 3 | Event / settlement-date correlation caps | **REFRAME → build a DAY cap** | Within-event clustering is trivially small (max 2 losses/event). The real correlation is **cross-event, same-day** (7 losses on 2026-07-12, Poisson p=0.4%). |
| 4 | Queue position (`live_price_offset_cents`) | **BUILD — the one live lever left** | Untested, and the only knob that acts on the ~2¢/contract adverse-selection gap that actually killed mmsell3 live. |
| 5 | Spread & depth filters | **DEAD in the cheap band** | 96% of cheap-band trades already sit at spread 0–2¢. No variance to filter on; in the rich band "wide spread" is just a restatement of "won't fill". |
| 6 | Outcome count | **DEAD as a filter** | 70% of candidate flow comes from 16+-outcome ladders. Filtering on outcome count deletes the book rather than improving it. |
| 7 | Book overlap | **CONFIRMED PROBLEM — not a fix** | mmsell9 ⊂ mmsell10 ⊂ mmsell3 ⊂ mmsell1 at **100% ticker overlap**. The 12 books are one book viewed 12 ways; summing their P&L multiply-counts the same trades. |
| 8 | **Scale into "guaranteed" winners** (operator) | **DEAD — execution kills it** | At yes-mid ≤3¢ an add costs **98.83¢ to collect 1.17¢** (break-even 98.8% win). Total upside over all history: **+$0.80**, erased by one adverse settlement. |
| 9 | **Locate the tail by market** (operator) | **CONFIRMED, and actionable** | Head-to-head in-play game winners are the tail engine: `KXMLBGAME` cheap runs **11.1% loss vs 5.5% break-even**, −5.70¢/trade. |

---

## 1. The governing constraint: we are out of statistical power, not out of ideas

Everything below is dominated by one fact. Deduplicated to **distinct markets** (the pooled
per-book counts multiply-count the same ticker — see §7), the entire cheap-band
(yes ≤7¢) history is:

> **n = 792 markets · 96.2% win · 30 losses · +1.69¢/trade (fill-everything paper)**

Break-even loss rate at a ~5.5¢ premium against a ~94¢ risk is **5.5%**. Our 3.8% observed loss
rate has a 95% Wilson interval of **[2.7%, 5.4%]** — it clears break-even, but *only just*, and
only when the whole book is pooled.

| cell (deduped) | n | losses | loss % | 95% CI | vs 5.5% break-even |
|---|---|---|---|---|---|
| **ALL cheap ≤7¢** | 792 | 30 | 3.8% | [2.7%, 5.4%] | **clears** |
| KXWCGOAL | 100 | 2 | 2.0% | [0.6%, 7.0%] | undecided |
| KXBTCD | 79 | 3 | 3.8% | [1.3%, 10.6%] | undecided |
| KXMLBTOTAL | 62 | 2 | 3.2% | [0.9%, 11.0%] | undecided |
| KXWCSCORE | 58 | 2 | 3.4% | [1.0%, 11.7%] | undecided |
| KXMLBGAME | 27 | 3 | 11.1% | [3.9%, 28.1%] | undecided (worst point estimate) |

**Not one individual cell is resolvable.** At a 3.8% true loss rate it takes **n ≈ 800 distinct
markets** for a cell's CI to exclude break-even. We accumulate roughly 800 cheap-band markets per
*quarter* across the entire book.

This is the operator's own point, quantified: the difference between "right 99% of the time" and
"right 94% of the time" is the difference between excellent and worthless, and at these sample
sizes we cannot tell them apart. **Therefore: every idea that SLICES the book into cells is
self-defeating — it buys a plausible filter at the cost of never being able to verify it.** The
ideas worth pursuing are the ones that act on the book *without* partitioning it: execution
(#4), exposure shape (#3), and sizing.

---

## 2. Idea #1 — Clip size / fee amortization: the premise is false

The thesis was that a 1-contract clip pays `ceil(0.07·C·p·(1−p))` rounded up to a whole cent —
1.00¢/contract on a 93¢ NO against an asymptote of 0.456¢ — so larger clips recover ~0.5¢/contract,
"the biggest immediate, certain win."

That arithmetic is correct **for the paper simulator**. It is not what live pays.

**Measured, from `fills` joined to `live_orders` on the two live mmsell epochs:**

| book | fills | contracts | total fee | **¢/contract** | paper charges |
|---|---|---|---|---|---|
| mmsell3 (q=1) | 366 | 366 | $0.047 | **0.0128¢** | 1.00¢ |
| mmsell10 (q=2) | 63 | 126 | $0.011 | **0.0089¢** | 1.00¢ |

Kalshi charges essentially **nothing** on these resting maker fills. The fee mmsell is
supposedly amortizing does not exist.

Two consequences, and they point in opposite directions — state both:

1. **Idea #1 is void.** Raising `max_order_size` above 1 buys no fee saving whatsoever. It only
   multiplies per-position exposure (at the current 60-position live cap, a 5× clip takes live
   exposure from ~$56 to ~$279). Do it for capital-deployment reasons if you want, but *not* for
   fees, and not as a "certain win."
2. **The paper books are mis-modelled by ~1¢/contract.** `paper/engine.py:kalshi_fee` applies the
   full taker fee to a maker entry. In the cheap band that is a flat, uniform −1.00¢ on every
   trade (the `ceil` floors at 1¢ for every NO price above 82¢). Because it is uniform it does
   **not** change the *ranking* of books — but every gate stated in absolute cents
   ("PROMOTE if > +2¢") is being judged against a number that is ~1¢ too pessimistic.

**This does not create P&L.** Live mmsell3 realized +0.18¢/trade and that is unchanged. Fixing the
fee model re-attributes ~1¢/contract from "fees" to "adverse selection" — it makes the paper→live
reconciliation honest, and it makes the absolute gates correct. Nothing more.

> **Action (no gate needed — this is a modelling correction, not an experiment):** teach the paper
> fee model that an mmsell/theta *maker* entry pays the observed maker rate, not the taker
> formula. Re-baseline the absolute-cent gates in `docs/BOOK_REGISTRY.md` afterwards.
> **Confirm against a Kalshi statement first** — n=492 contracts across two epochs is a solid
> measurement but it is our own bookkeeping, and a fee schedule that varies by series would show
> up as exactly this pattern.

---

## 3. Idea #2 — Ladder overround: measured, and it carries no incremental signal

The idea is well-motivated: for a mutually-exclusive multi-outcome event, summing every outcome's
YES mid measures the favorite-longshot bias *directly* rather than via a static price band. Nothing
in the codebase computed it. Now `scripts/mmsell_ladder_probe.py` does (ops: `mmsell_ladder_probe`).

**Result over a 400-event live board snapshot:**

| shape | events | candidates | cand share | overround defined? |
|---|---|---|---|---|
| PARTITION (mutually exclusive) | 183 | 73 | **46.5%** | yes |
| THRESHOLD (nested over/under) | 126 | 71 | 45.2% | **no** |
| BINARY | 36 | 3 | 1.9% | no |
| THIN | 55 | 10 | 6.4% | no |

The premise everyone skips: **Kalshi's sports "ladders" are mostly nested thresholds** ("Over 6.5
runs", "Over 7.5 runs"…). Their YES legs are a survival function, not a partition — summing them
gives a median of **287** and a p90 of **1383**, pure garbage. Thresholding on that would be
thresholding on strike count. Only the 46.5% PARTITION share can be read at all.

Where it *is* defined, there is genuine variance (p10 99.4, median 105.0, p90 122.0 — a 22.6-point
spread), so the idea is not absurd. But the decisive test:

> partition events **with** an mmsell candidate: n=49, median overround **104.5**
> partition events **without** an mmsell candidate: n=134, median overround **105.5**

Candidate-bearing events are, if anything, *marginally less* overround than the rest. **The cheap
price band already captures whatever overround would have told us** — a market is in our band
because its tail is overpriced, which is the same information, measured per-leg instead of
per-event.

**Verdict: PARK.** Capped at 46.5% of flow, showing no incremental selection power, and any
"trade only when overround > X" rule would halve n again in a book that (§1) already cannot
resolve its cells. The probe is committed so this can be re-checked cheaply, but the expensive
forward-collection + settlement study is **not** worth building.

---

## 4. Idea #3 — Correlation caps: right instinct, wrong axis

mmsell has no `max_per_event` while theta/tfav/pin15 all do. But the data says an event cap
solves the wrong problem.

**Within-event clustering is small.** Deduplicated cheap-band markets grouped by event:

| markets in event | markets | win% | ¢/trade | events | events w/ ≥1 loss | max losses in one event |
|---|---|---|---|---|---|---|
| 1 | 370 | 97.3% | +2.76 | 370 | 10 | **1** |
| 2 | 142 | 95.1% | +0.51 | 71 | 7 | **1** |
| 3–4 | 102 | 95.1% | +0.57 | 30 | 5 | **1** |
| 5+ | 178 | 95.5% | +1.04 | 24 | 7 | **2** |

No event has ever produced more than 2 cheap-band losses. A `max_per_event` cap would have
prevented at most one loss in the entire history. (Mechanically sensible: only ~2 legs of a big
ladder are ever simultaneously cheap — `mmsell_candidate_ticks` shows 214 events with 2 in-band
tickers vs 10 with 9+.)

**The real correlation is same-day, cross-event.**

| date | cheap markets settled | win% | losses | ¢/trade |
|---|---|---|---|---|
| 2026-07-12 | 51 | 86.3% | **7** | **−8.33** |
| 2026-08-02 | 11 | 81.8% | 2 | −12.64 |
| (typical good day) | 40–80 | 95–97% | 2 | +1 to +2 |

At a 3.8% base rate, 51 settling markets should produce **1.9** losses. Seven is a Poisson
p = **0.38%** event — not ordinary variance. And those 7 losses spanned **six different series**
(MLBGAME ×2, MLBSPREAD, PGATOUR, WCGOAL, WCMOV, WCSOA) across 6 different events. A per-event cap
would have stopped **one of the seven**.

Something makes cheap longshots hit together across unrelated sports on the same day. 7 of the
book's 30 all-time cheap-band losses landed on that one date.

> **Pre-registered gate — `max_per_day` / daily-exposure cap.** Build a *paper* variant of
> mmsell10 with a cap on total new cheap-band entries opened per settlement-date (candidate: 25).
> At **n ≥ 300 settled**, PROMOTE if **5th-percentile DAILY P&L improves by ≥ 30%** AND mean
> ¢/trade ≥ control − 0.3¢. KILL if mean is below control − 0.5¢, or if daily-p5 is unchanged
> (the clustering was luck, not structure). Note this trades *total return* for *smoothness* by
> construction — it is a drawdown control, not an edge, and must be judged as one.
> **This must land before November** — election ladders are the acute case for same-day
> correlation, and they are all one settlement date.

---

## 5. Idea #4 — Queue position: the only live lever that touches the actual problem

`mmsell_live_price_offset_cents` exists, is set to `0` (join the no-bid), and has never been
tested. It is the **only** untested knob that acts on the mechanism that actually killed mmsell3
live: adverse selection on maker fills, worth roughly **2¢/contract** (paper gross ~+2.2¢ vs live
realized +0.18¢, once §2's fee correction is applied).

The trade is explicit: bidding 1¢ above the no-bid costs 1¢ of edge on every fill, and buys a
higher fill rate plus *earlier queue position* — and earlier queue position is precisely what
determines whether you get the quiet fills or only the ones informed flow chooses to hand you.
The live retry data already showed the missed set earned the same as the captured set in paper
(6.15 vs 6.26¢/contract), i.e. **the misses are lost volume, not dodged bullets** — which is the
argument for paying to fill more of them.

We cannot answer this from the current data: the offset has only ever been 0, so there is no
variation to measure. It requires a live A/B.

> **Pre-registered gate — offset A/B.** Run `mmsell10` live with `offset = 0` and a parallel
> twin/epoch at `offset = 1`, both under the standing live/paper twin protocol
> (`docs/LIVE_PAPER_TWIN.md` — no strategy goes live without a twin). At **n ≥ 150 fills per
> arm**, PROMOTE `offset = 1` if realized ¢/contract is **> offset-0 by ≥ 0.5¢**; KILL if it is
> below offset-0 at all. Report fill rate alongside — a higher fill rate with *worse* realized
> P&L is the signature of buying adverse selection, and is a kill, not a puzzle.

---

## 6. Idea #5 — Spread & depth: no room in the band we actually trade

The spread *is* the maker edge, so filtering on it sounds free. Reconstructed for the full history
(yes-ask and mid are both recoverable from `fill_assumption`, so `spread = 2·(yes_ask − mid)`):

| spread | band | n | win% | ¢/trade | avg yes px |
|---|---|---|---|---|---|
| 0–2¢ | cheap ≤7 | **2498** | 96.6% | +2.00 | 6.4 |
| 3–4¢ | cheap ≤7 | 113 | 100.0% | +6.00 | 7.0 |
| 5+¢ | cheap ≤7 | **0** | — | — | — |
| 0–2¢ | rich 8+ | 6724 | 85.8% | +0.96 | 16.5 |
| 3–4¢ | rich 8+ | 971 | 89.6% | +3.12 | 14.8 |
| 5–8¢ | rich 8+ | 740 | 91.1% | +4.28 | 14.4 |
| 9–15¢ | rich 8+ | 260 | 93.5% | +11.01 | 19.0 |
| 16+¢ | rich 8+ | 169 | 94.1% | **+20.75** | 28.6 |

Two reasons this is not the free lunch it looks like:

1. **In the cheap band there is nothing to filter.** 96% of cheap trades already sit at spread
   0–2¢ and *none* exceed 4¢ — arithmetically forced, since a ≤7¢ yes-ask cannot straddle a wide
   spread. For mmsell10's regime the filter has no variance to act on.
2. **In the rich band, "wide spread" is a restatement of "will not fill."** That +20.75¢ at a 29¢
   spread is paper's fill-everything fantasy: a resting order at the yes-ask of a 29¢-wide book is
   not going to be lifted by anything except informed flow. This is the same mirage
   `docs/MMSELL_FILL_MODEL.md` already root-caused, wearing a new label.

**Verdict: DEAD.** Depth is not even recorded on `mmsell_candidate_ticks` (no `top_depth` column),
so a depth filter would need new collection to test a hypothesis that the spread result gives no
reason to hold.

---

## 7. Ideas #6 and #7 — outcome count, and the overlap problem

**#6 Outcome count — DEAD as a filter.** From the same 400-event board scan:

| markets/event | events | candidates | candidates/event |
|---|---|---|---|
| 2 (binary) | 91 | 13 | 0.14 |
| 3–4 | 38 | 7 | 0.18 |
| 5–8 | 55 | 9 | 0.16 |
| 9–15 | 71 | 18 | 0.25 |
| **16+** | 145 | **110** | **0.76** |

**70% of all candidate flow comes from 16+-outcome ladders.** Outcome count is very nearly
collinear with "has a cheap tail at all" — filtering on it deletes the book rather than refining
it. (It is not stored on settled trades, so a P&L-by-outcome-count cut would need new collection;
given the structure above, it would mostly be measuring one bucket against noise.)

**#7 Book overlap — CONFIRMED, and it is a measurement problem, not a diversification one.**

| pair | shared tickers | overlap (of the smaller book) |
|---|---|---|
| mmsell10 ∩ mmsell3 | 295 | **100.0%** |
| mmsell10 ∩ mmsell6 | 295 | **100.0%** |
| mmsell9 ∩ mmsell10 | 89 | **100.0%** |
| mmsell11 ∩ mmsell3 | 536 | **100.0%** |
| mmsellA4 ∩ mmsell10 | 24 | **100.0%** |
| mmsell11 ∩ mmsell10 | 282 | 95.6% |

The books are **nested subsets by construction** (mmsell9 ⊂ mmsell10 ⊂ mmsell3 ⊂ mmsell1 ⊂
mmsell). Running eleven of them is running one book with eleven overlapping views. Two real
consequences:

- **Running several is not diversification.** If they were ever armed together, position limits
  and correlation would be far worse than the per-book caps imply.
- **Any pooled figure across books multiply-counts the same trade.** The 4,498+2,967+1,953+…
  headline counts collapse to **792 distinct cheap-band markets** (§1). Effective sample is even
  smaller for ladder series — `KXTRUMPSAY` shows 31 markets across only **4 events**, and
  `KXWCMENTION` 40 markets across 15. Those are the cells whose "100% win rate" looks most
  impressive and is most illusory.

> **Action:** the loop checker and any promote decision must read **distinct-ticker** (and, for
> ladder series, **distinct-event**) counts, not pooled `paper_trades` rows. No gate needed — this
> is a reporting correction.

---

## 8. Operator idea — scaling size into near-certain winners

**The thesis:** stops and volatility gates do not reduce the tail (correct — see §10), so instead
lean harder into positions that have become near-certain as they approach settlement (e.g. a BTC
daily at ~99% with a few hours left), and let the extra premium offset the tails.

The survival surface is real and supports the *observation*. Position-weighted, over every
cheap-band position with a captured intraday path:

| state reached | positions | win% | losses | ¢/trade |
|---|---|---|---|---|
| reached yes-mid ≤3¢ with <1h left | **68** | **100.00%** | **0** | +5.56 |
| reached yes-mid ≤7¢ (not ≤3) with <1h left | 15 | 93.33% | 1 | −1.27 |
| never reached either | 95 | 96.84% | 3 | +2.44 |

68 positions, zero losses. The instinct is well-founded. **The execution is what kills it.**

**What an add actually costs.** From `mmsell_position_ticks`, at yes-mid ≤3¢ inside the last 3
hours, the book averages `yes_bid 1.17 / yes_ask 2.45 / no_bid 97.55`:

| route | you pay | you collect | break-even win rate |
|---|---|---|---|
| add as **taker** (buy NO at no-ask = 100 − yes_bid) | **98.83¢** | **1.17¢** | **98.8%** |
| add as **maker** (rest NO at no-bid) | 97.55¢ | 2.45¢ | 97.6% |
| *(original entry, for comparison)* | 93.50¢ | 6.50¢ | 93.5% |

To break even on the taker add you need to be right **98.8%** of the time. Our observed 68/68 has
a 95% Wilson upper bound on the loss rate of **5.35%** — the data is consistent with losing badly.
Establishing 98.8% would take roughly **n ≥ 400** clean observations at that exact state; we have
68, accruing at maybe 70/month. And the maker route is worse than it looks: resting at the no-bid
means you fill only when someone actively sells into you, which is the adverse-selection channel
that already costs this book ~2¢/contract.

**Sizing the prize.** 68 adds × 1.17¢ = **+$0.80** of premium across the entire capture window.
One adverse settlement costs **−98.83¢**. The whole strategy's historical upside is erased by a
single loss, and it needs ~85 consecutive wins to pay for one.

**And it works directly against the stated goal.** The add is in the *same market* as the original
position — the two legs are 100% correlated and settle on one event. You cannot diversify against
yourself:

| | premium if right | loss if wrong |
|---|---|---|
| hold 1 contract | +5.50¢ | −93.50¢ |
| hold 1 + add 1 late | +8.50¢ (**+55%**) | **−190.50¢ (+104%)** |

Adding doubles tail severity to raise premium by half. That is the opposite of offsetting the tail.

**Verdict: DEAD as specified.** Not because the win rate isn't high — it is — but because the
premium collapses faster than the win rate rises, and the added exposure is perfectly correlated
with the risk it is meant to hedge.

**The salvageable version.** The correct response to "some cells are much safer than others" is
not to add size *late at worse odds*, it is to size the *original entry* by the cell's measured
tail rate — bet more where premium-per-unit-risk is highest at the moment of entry. That is
ordinary variance-targeted (Kelly-flavoured) sizing, it acts at the only point where the odds are
good, and it does not touch correlation. The per-cell inputs already exist (§9's table).

> **Pre-registered gate — tail-weighted entry sizing.** Paper variant of mmsell10 sizing each
> entry as `clip × f(cell)` where `f` is a bounded (0.5×–2×) function of the cell's trailing
> loss rate vs its premium, cell = series, **with a hard rule that a cell needs ≥ 50 distinct
> events before it may be sized above 1×** (§1: cells below that are noise, and §7: markets ≠
> events for ladders). At **n ≥ 400 settled**, PROMOTE if **¢-per-dollar-at-risk** beats flat
> sizing by ≥ 15% AND 5th-percentile daily P&L is not worse. KILL if either fails — flat sizing
> is then correct and the cells are indistinguishable, which is the §1 null and a legitimate
> result.

---

## 9. Operator idea — where the tail actually lives

**The thesis:** find which markets carry the highest tail rate, understand the underlying
situation (e.g. a game one score from flipping), and stop trading those. **Confirmed.**

Deduplicated to distinct markets, cheap band (yes ≤7¢), all entries at ~6.5¢ so the ~5.5%
break-even loss rate applies uniformly:

| series | markets | events | losses | **loss %** | ¢/trade | read |
|---|---|---|---|---|---|---|
| KXWCMENTION | 40 | 15 | 0 | 0.0% | +5.55 | few events — see §7 |
| KXWCGOAL | 100 | 98 | 2 | 2.0% | +3.46 | **best real sample** |
| KXMLBTOTAL | 62 | 43 | 2 | 3.2% | +2.26 | good |
| KXTRUMPSAY | 31 | **4** | 1 | 3.2% | +2.39 | 4 events — illusory n |
| KXWCSCORE | 58 | 19 | 2 | 3.4% | +2.07 | good |
| KXBTCD | 79 | 27 | 3 | 3.8% | +1.65 | good |
| KXMLBSPREAD | 22 | 17 | 1 | 4.5% | +1.00 | marginal |
| KXWC1HSCORE | 22 | 14 | 1 | 4.5% | +0.82 | marginal |
| KXWCSOA | 21 | 10 | 1 | 4.8% | +0.86 | marginal |
| KXWCMOV | 20 | 13 | 1 | 5.0% | +0.50 | marginal |
| KXPGATOUR | 18 | 6 | 1 | 5.6% | −0.56 | negative |
| **KXMLBGAME** | **27** | **27** | **3** | **11.1%** | **−5.70** | **the tail engine** |

**The pattern is the market's *structure*, not its sport.** The offenders are **head-to-head,
in-play "who wins" markets** — `KXMLBGAME` (avg hold 1.2h, i.e. deep into the game) at 11.1%, and
in the wider pooled cut `KXATPMATCH` at 10.0% (−4.65¢). Everything that is a **total, spread,
prop, or scheduled-settle** market is at or under 3.5%. Note it is not "tennis" or "baseball"
wholesale: `KXITFMATCH`, `KXITFWMATCH`, `KXWTAMATCH`, `KXMLBHR`, `KXMLBSPREAD`, `KXMLBTOTAL` cheap
tails are all fine. It is specifically the h2h game/match winner.

**The mechanism is exactly the one proposed.** A 5–7¢ cheap tail on an in-play game winner is a
team that is behind late and can still come back. The market prices that at 5–7%; it happens
**11%** of the time. Baseball has no clock — a two-run deficit in the 8th is a live comeback, and
the favorite-longshot bias *inverts* there because the "longshot" is a genuinely live outcome that
casual pricing rounds down. In a total or a spread there is no equivalent single discrete event
that flips the outcome.

**The honest caveat, and it matters:** n=27 with 3 losses gives a 95% CI of **[3.9%, 28.1%]** —
which does not formally exclude the 5.5% break-even. This is §1 biting. The finding is a strong
*mechanistic* hypothesis with a consistent structural story across two independent series
(MLBGAME, ATPMATCH) — it is **not** yet a statistically established cell.

That said, the asymmetry favours acting: mmsell10's `maxyes` regime is already the good cell, and
excluding h2h in-play winners costs little flow (27 markets of 792 ≈ 3.4%) while removing the
worst point estimate in the book.

> **Pre-registered gate — h2h in-play exclusion (`mmsell12`).** Paper variant of mmsell10
> (`lo=5,hi=10,maxyes=7`) additionally skipping head-to-head game/match-winner series while
> in-play. At **n ≥ 250 settled** in the *control*, PROMOTE if `mmsell12` ¢/trade **> mmsell10 by
> ≥ 0.75¢** AND its loss rate is below mmsell10's. KILL if ¢/trade ≤ mmsell10 — the MLBGAME
> reading was small-sample noise and the exclusion cost us flow for nothing. Report the excluded
> cohort's own P&L each check so the counterfactual stays visible.

### 9a. RESOLVED 2026-08-03 — the gate FAILED, and the finding inverted

`scripts/mmsell_h2h_study.py` (ops: `mmsell_h2h_study`) took the question to Kalshi's own settled
history and built **1,137 entries across 2,074 settled markets** — 42× the n our paper book could
supply. The pre-registered structural hypothesis was that **unclocked** sports (baseball, tennis,
cricket — no way to run out the clock, so the trailing side always retains a live path) would
carry the fat cheap tail, while **clocked** sports (soccer, basketball, hockey) would not.

| cohort | n | losses | loss % | 95% CI | break-even | ¢/trade | verdict |
|---|---|---|---|---|---|---|---|
| **UNCLOCKED** | 586 | 31 | **5.3%** | [3.8, 7.4] | 8.1% | **+2.85** | **EARNS** — CI excludes break-even |
| **CLOCKED** | 551 | 44 | **8.0%** | [6.0, 10.6] | 7.8% | **−0.23** | undecided / breakeven |
| all h2h | 1137 | 75 | 6.6% | [5.3, 8.2] | 8.0% | +1.36 | |

**The hypothesis is refuted, and it points the other way.** Unclocked h2h is the *profitable*
cohort; clocked h2h is the breakeven-to-negative one. By sport: tennis +3.47¢, baseball **+2.19¢**
(75 entries, 5.3% loss vs a 7.5% break-even), cricket +1.53¢ — against soccer −0.28¢ (375 entries)
and basketball +0.48¢.

**So the roadmap's own KXMLBGAME reading was small-sample noise**, exactly as its CI warned
(n=27, [3.9%, 28.1%]). At 42× the sample, baseball h2h is fine. **`mmsell12` is NOT built** — the
gate failed and the exclusion is not justified. This is the pre-registration doing its job: had
we acted on the n=27 point estimate we would have cut a +2.19¢ cohort.

**But a much stronger axis fell out of the same data — time to close:**

| window | cohort | n | loss % | break-even | ¢/trade | p5 |
|---|---|---|---|---|---|---|
| **< 1h** | unclocked | 335 | **1.2%** | 7.8% | **+6.57** | **+4.0** |
| < 1h | clocked | 330 | 7.6% | 7.7% | +0.08 | −91.0 |
| **1–2h** | unclocked | 143 | 10.5% | 8.9% | **−1.64** | −91.0 |
| 1–2h | clocked | 168 | 10.1% | 8.0% | **−2.10** | −92.0 |
| **2–4h** | unclocked | 76 | **14.5%** | 8.7% | **−5.75** | −91.0 |
| 4h+ | both | 61 | ~1.6% | ~7.1% | +5.4 | +3.0 |

The dominant variable is **not the sport, it is how close to settlement the entry is.** The final
hour is where essentially all the money is (+6.57¢ at a 1.2% loss rate, and a *positive* 5th
percentile — the tail barely exists there); the 1–4h window is where both the mean and the tail
are worst (−1.6 to −5.8¢, p5 ≈ −91¢).

**Three caveats, all load-bearing:**

1. **This is exploratory, not validated.** The htc cut was found by slicing the same dataset that
   refuted the pre-registered hypothesis. It is a hypothesis *generated* from this data and must
   be pre-registered and tested out-of-sample before anything is built on it.
2. **It is fill-everything.** No adverse-selection haircut is applied, and the final hour of an
   in-play market is exactly where a resting maker order is most likely to be picked off — mmsell's
   own live decomposition found late/in-play entries were the adversely-selected ones. The
   backtest says the *price path* is favourable there; whether a **maker** can capture it is a
   different question, and the one `mmsell fill model` exists to answer.
3. **The price band is richer than the live book's.** 783 of the 1,137 entries sit at 8–11¢, above
   mmsell10's `maxyes=7` cap. Inside mmsell10's actual 5–8¢ band the split is: unclocked +2.77¢
   (2 losses / 81), clocked **−1.42¢** (7 losses / 106) — same direction, but both undecided at
   that n.

> **Pre-registered gate — final-hour concentration (`mmsell13`), REPLACING the killed mmsell12.**
> Paper variant of mmsell10 restricted to `htcmax = 1`. At **n ≥ 250 settled**, PROMOTE if
> ¢/trade **> mmsell10 by ≥ 1.0¢ AND** its 5th-percentile P&L is no worse. KILL if ¢/trade
> ≤ mmsell10 — the htc effect was an artifact of the fill-everything assumption, which is the
> single most likely way this dies. **Live promotion additionally requires `mmsell fill model`
> realizable ¢/trade**, because caveat 2 above is precisely where the paper→live gap lives. Note
> this contradicts nothing prior: `mmsell11` (`htcmin=6`) and `mmsell7` (`htcmax=24`) both cut at
> the wrong granularity to see a one-hour effect.

---

## 10. Why the existing tail controls did not work (the operator's premise, verified)

The premise behind ideas #8/#9 was that stops and volatility gates fail to reduce tail risk. The
anchor set now has enough data to confirm it — **but only when the stopped-out trades are counted.**
Reading `status='settled'` alone (as the pooled book views do) silently drops every position the
stop actually closed, which is exactly the trades that measure the stop.

| book | mechanic | n (settled + stopped) | ¢/trade | win% | p5 | worst |
|---|---|---|---|---|---|---|
| **mmsell10** | hold (control) | 295 | **+3.14** | 97.6% | **+5.0** | −95.0 |
| mmsellA1 | stop yes-bid ≥12¢, K=2 | 44 | **−4.16** | 47.7% | −19.0 | −41.0 |
| mmsellA2 | stop ≥20¢, K=2 | 30 | **−2.67** | 73.3% | −30.8 | −41.0 |
| mmsellA3 | stop ≥30¢, K=2 | 27 | **−6.44** | 81.5% | −63.7 | −94.0 |
| mmsellA4 | volatility entry gate | 24 | **−7.13** | 87.5% | −94.0 | −95.0 |

The stops **do** cap worst-case severity (−41¢ vs −95¢). They fail anyway, on both halves of the
pre-registered gate in `docs/MMSELL_EXIT_STUDY.md` (Δp5 up **AND** Δmean ≥ −0.3¢):

- **Δmean is catastrophic**: A1 costs **−7.3¢/trade** against the control — more than twice the
  entire edge.
- **Δp5 is negative, not positive.** The control wins 97.6% of the time, so its 5th percentile is
  still a *win* (+5.0¢). A1 stops out **52% of the time** (23 of 44), so its 5th percentile is a
  loss (−19¢). The stop makes the ordinary tail metric *worse*; it only helps in the extreme
  1st-percentile case.

A rule that fires on half the book is not tail insurance, it is a different — and losing —
strategy, which is precisely the failure mode the exit-study doc pre-registered against. Cheap
tails routinely wobble up through 12¢ and come back; the stop converts winners into losers.

These books are at n=24–44 against a pre-registered n≥100, so this is **directional, not a final
kill** — but the mechanism is clear and the direction is not marginal.

---

## 10a. So what *does* reduce the tail?

Collecting every result in this doc, the honest headline is a reframe:

> **This book does not have a tail problem. It has a margin problem.**

`mmsell10`'s 5th-percentile trade is **+5.0¢ — a win**. It loses 2.4–3.8% of the time at ~−94¢,
and that is not a defect to be engineered away: it *is* the product. We are selling insurance on
cheap tails, and the payout is supposed to be lumpy. Decomposed per trade:

| | ¢/trade |
|---|---|
| premium collected (97.6% of the time) | +5.40 |
| tail paid (2.4% × −94.3¢) | **−2.26** |
| net | **+3.14** |

The tail costs 2.26¢ of a 5.40¢ gross. Eliminating it entirely is worth +2.26¢ — but **every
mechanism that reduces tail frequency also reduces premium**, and §10 measured the exchange rate:
the L12 stop bought −41¢ worst-case instead of −95¢ and paid **−7.3¢/trade** for it. That is ~3×
the entire value of the tail it was insuring against. Any future tail control has to beat that
arithmetic, and the bar is brutal.

**What is ruled out, and why (stop re-testing these):**

| mechanism | status | why it fails |
|---|---|---|
| confirmed stop-loss (A1–A3) | measured, dead | fires on 52% of positions; −7.3¢/trade; p5 gets *worse* |
| volatility entry gate (A4) | measured, dead | −7.13¢/trade at n=24 |
| take-profit / early exit | measured (prior work) | hold-to-settlement wins on mean and Sharpe |
| adding into winners | measured, dead (§8) | doubles tail severity for +55% premium; legs 100% correlated |
| per-event cap | measured, no room (§4) | no event has ever produced >2 cheap-band losses |
| h2h exclusion | gate failed (§9a) | the cohort earns +2.85¢ at 42× the sample |

**What is left, ranked by evidence:**

1. **Fix `mmsellA5` (the short strangle) — the only *structural* tail reducer in the design.**
   Selling both mutually-exclusive cheap tails of one event collects two premiums against **at
   most one loss**, because one settlement cannot make both tails hit. That is genuine tail
   reduction rather than tail insurance bought at a bad price — it is the only idea here that
   improves the payoff *shape* instead of trading mean for variance. It has never traded (§12), and
   this PR fixes the cause: `_event_has_both_tails` read the bare `yes_bid`/`yes_ask` keys, which
   the nested event payload no longer carries (it serves the dollar-string form, and
   `weather/tracker.py` already carried the fallback that mmsell never got). Its gate is already
   pre-registered in `docs/MMSELL_ANCHOR_SET.md` (n ≥ 82 clean pairs, 95% lower bound on pair win
   rate clearing 93.9%) — it now needs to actually accrue data.
2. **Daily exposure cap (§4).** Targets the one correlation that measurably exists: 7 losses in a
   single day against 1.9 expected (Poisson p = 0.38%), spanning 6 unrelated series. Bounds
   drawdown rather than per-trade severity, which is the axis the loss data actually shows.
3. **Final-hour concentration (§9a, `mmsell13`).** The rare candidate that improves mean *and*
   tail together — the <1h cell runs a 1.2% loss rate with a **positive** 5th percentile, against
   −91¢ p5 in the 1–4h window. Exploratory and fill-everything, so it must clear its own gate and
   the fill model before it means anything, but it is the only lever pointing both directions at
   once.
4. **True diversification — more distinct markets per unit of capital.** Note this is *not* more
   books: §7 showed the books are 100%-overlapping nested subsets, so running eleven of them
   diversifies nothing. It means more distinct tickers at smaller clips. §2 is what unlocks this:
   with live maker fees measured at ~0.013¢/contract there is **no longer any fee-amortization
   argument against small clips**, which was the only reason to prefer size over count.

Everything else on the list trades mean for variance at a rate the stop experiment already proved
we cannot afford.

## 11. Sequencing

**Now — corrections, no experiment required:**
1. Fix the paper maker-fee model (§2), then re-baseline absolute-cent gates.
2. Make all book reporting read distinct-ticker / distinct-event counts (§7).
3. Read the anchor books as `settled + closed_sl` everywhere (§10) — the current view is biased.

**BUILT 2026-08-03 (this PR):**
4. **Live queue-offset A/B** (§5) — `docs/MMSELL_OFFSET_AB.md`. Randomized per-ticker within one
   book, so live exposure is unchanged. **Inert** until `MMSELL_LIVE_OFFSET_AB_ARMS` is set;
   arming is an operator decision because it puts real money on both arms.
5. **`mmsellA5` strangle gate fixed** (§10a) — the book could never enter; it now can, and its
   pre-registered gate starts accruing.
6. **h2h structural study** (§9a) — gate FAILED, `mmsell12` not built, KXMLBGAME was noise.

**Next:**
7. **Daily-exposure cap** (§4) — the only measured, non-slicing correlation control. *Before
   November.*
8. **`mmsell13` final-hour concentration** (§9a) — paper only, and it must clear the fill model
   before it means anything live.
9. Tail-weighted entry sizing (§8) — paper, cheap, not urgent.

**Parked, with reasons on record:** clip size (§2), ladder overround (§3), spread/depth (§6),
outcome count (§7), per-event caps (§4), late-add sizing (§8), h2h exclusion (§9a — gate failed).

---

## 12. Operational anomalies found while doing this (not part of the roadmap)

Two things surfaced that are unrelated to strategy but should not sit unreported:

1. **`mmsell3_closeout` is stuck in a rejected-order loop.** 1,942 live orders with status
   `rejected` (`invalid_order` / `invalid_parameters`), concentrated on ~8 tickers and retried
   160–644 times each (`KXRT-ODY-95` ×644, `KXTRUMPSAY-26JUL20-URAN` ×644, six
   `KXWCMENTION-MENWORLDCUP-*` ×36–163). They are trying to close positions in markets that have
   already expired, at ~4–6¢ limits. `mmsell_closeout_enabled` appears to have been left on after
   the mmsell3 wind-down. Costs no money but is hammering the API.
2. **`mmsellA5` (the short strangle) has never traded — zero rows in `paper_trades`.** Its entry
   requires `_event_has_both_tails`, which reads `yes_bid`/`yes_ask` straight off the nested event
   payload. This probe hit exactly that: those keys were absent from the nested markets and every
   event read as unpriced until a `*_dollars` / `*_cents` fallback was added
   (`scripts/mmsell_ladder_probe.py`). **The strangle gate is likely always False for the same
   reason** — worth checking before concluding anything about A5.
