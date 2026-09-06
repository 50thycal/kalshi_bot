# KXNFLSPREAD — the loss cell, adjudicated

**Investigated 2026-09-06, Research Lab.** Evidence: `ops` channel read-only queries
`nflspread-shape-1`, `nflspread-contest-2`, `nflspread-live-3b`, `nflspread-contest-pnl-4b`
against production Postgres. Detector shipped alongside: `scripts/mmsell_series_pnl.py`.
**Recorded as `XOS-000022`** — STRATEGY, owner `RESEARCH_LAB`, P1/MEDIUM, scoped to
`mmsell-price-ceiling` v2 (the experiment `Cmmsell10` resolves to). Status `INVESTIGATING`; the
proposals below are recorded as a *proposed fix* and the §7 gate as its *validation plan*. **No
disposition is recorded** — `NEW_VERSION` before the out-of-sample read would prejudge the gate.

> ## Verdict: **A — real, and narrower than it looks.**
>
> The single number: **−10.0¢ per trade, equal-weighted over the 44 contests the cell
> actually spans**, bootstrap 95% CI **[−18.1, −2.7]**, P(mean ≥ 0) = 0.002. That clears
> break-even at the honest independence unit, which is rare on this book.
>
> And the single caveat that bounds it: **every one of the 382 markets is NFL *preseason*.**
> They settled 2026-08-14 → 2026-08-30. The regular season began 2026-09-03. We have **zero**
> regular-season KXNFLSPREAD evidence, and the population we are exposed to going forward is
> not the population that was measured.

---

## 1. What the loss actually is

382 distinct markets, 1,554 book-trades across 30 books, **−$166.55**, −10.72¢/trade.

| cut | reading |
|---|---|
| **entry band** | −$129.16 of it (78%) is at a tail price **≤ 7¢** — the live `mmsell10` regime |
| **side** | −$166.61 on the ordinary leg (buy NO); the 17 strangle mirror legs are +$0.06 |
| **window** | preseason weeks 1–2 **−$174.13**; preseason week 3 **+$7.59** |
| **contests** | 44 games. 24 profitable, 20 losing |
| **concentration** | 2 games = 48% of the loss; 5 games = 87%; the other 33 games are +$47.8 |

**The first query got this wrong, and it is worth recording why.** `paper_trades.assumed_price`
is what the engine *paid* — mmsell sells a cheap YES tail by **buying NO**, so an ordinary entry
is stored at 91–94¢. Read raw, every trade lands in a "21¢+" band and the cheap-band cut — the
one that decides whether the live books are exposed — silently vanishes. The tail's own price is
`100 − assumed_price`. `tests/test_mmsell_series_pnl.py` pins this.

### It is not a handful of catastrophic settlements

That was the null this investigation expected to land on, and it does not survive. Concentration
is real in *dollars*, but not in *expectation*:

| measure | value |
|---|---|
| pooled ¢/trade | −10.72¢ |
| **equal-weighted by contest** (each game one observation) | **−10.00¢** |

Those are the same number. If the cell were a few disasters riding on an otherwise fair book,
equal-weighting by contest would pull the mean sharply toward zero. It does not. The
concentration multiplied the *variance* of an already-negative per-position expectation; it did
not create the sign.

Robustness, bootstrap on the 44 per-contest ¢/trade values (200k resamples):

| sample | mean | 95% CI | P(mean ≥ 0) |
|---|---|---|---|
| all 44 contests | −10.00¢ | [−18.07, −2.72] | 0.002 |
| drop the worst 1 | −8.36¢ | [−15.92, −1.56] | 0.006 |
| drop the worst 2 | −6.74¢ | [−13.72, −0.54] | 0.015 |
| drop the worst 3 | −5.33¢ | [−11.79, **+0.42**] | 0.036 |
| drop the worst 5 | −2.54¢ | [−7.93, +2.17] | 0.167 |

**So the result survives deleting the two worst games and dies on the fifth.** State it that way
rather than as a p-value: it is a genuine negative mean carried substantially by its left tail —
which is what selling tails looks like when the premium is mispriced, and also what noise looks
like at n=44. The sign test is honest about the tension: **20 of 44 contests negative, p = 0.77.**
The mean is negative; the *median contest is positive*.

### Against its own break-even, not against zero

In the cheap band (the live regime): 1,102 trades, 195 losses = **17.70%**, average tail sold at
**6.58¢**, average loss −93.4¢. A cell that collects W and pays L breaks even at `W/(W+|L|)`:

> **break-even 6.58% · observed 17.70% · `edge` = −11.1pp**

At the market level (174 distinct cheap-band markets, 27 losers = 15.5%) that is z = 4.8 against
break-even — but markets on one game are one bet, so read the contest table above instead.

---

## 2. Is it the cheap band? Yes — which is the bad answer

`docs/MMSELL_MARKET_TYPES.md` trap 2 usually rescues these findings: the pooled history is ~80%
old wide-band books entering at 12–20¢, so a loss cell is often somebody else's price. Not here.

| tail price | trades | P&L | loss% | ¢/trade |
|---|---|---|---|---|
| **≤ 7¢** | 1,102 | **−$129.16** | 17.7% | **−11.72** |
| 8–11¢ | 266 | −$24.04 | 18.1% | −9.04 |
| 12–20¢ | 113 | −$9.73 | 22.1% | −8.61 |
| 21¢+ | 73 | −$3.61 | 28.8% | −4.95 |

The loss lives squarely in `maxyes=7`. The `mmsell10` lineage is not a bystander.

---

## 3. Per book, and what real money actually lost

Paper, per book (excerpt): `mmsell5` −$33.10, `mmsell10` −$16.79, `mmsell9` −$16.40,
`Tmmsell6` −$15.89, `mmsellA4` −$14.20, **`Lmmsell10` −$8.65**, **`Cmmsell10` +$1.91**.

**Real money did trade it.** 26 fills, 33 contracts, average NO price 93.2¢, 2026-08-12 →
2026-08-29, across `mmsell10a`, `mmsell10b`, `mmsell10`, `Lmmsell10`, `Cmmsell10`:

> **Live realized: −$3.71** (6 of 33 contracts settled against us; 26 distinct markets.)

Small — because preseason supply is small and the live books were rate-limited and mostly
cancelled (of 111 live KXNFLSPREAD orders, only 26 filled). **That is the whole point of the
finding.** `Dmmsell10` was still placing KXNFLSPREAD orders on 2026-09-02, the series is
`GRADUATED`, and NFL regular season lists roughly 16 games a week for 18 weeks against 44 games
of preseason total. The exposure measured is not the exposure ahead.

---

## 4. Contest clustering — the contest cap addresses the dollars, not the sign

The 44 contests, bucketed by how many of our markets on that game lost:

| losing markets on the game | contests | our markets | P&L |
|---|---|---|---|
| 0 | 19 | 151 | **+$50.40** |
| 1 | 7 | 52 | +$4.28 |
| 2 | 5 | 39 | −$8.83 |
| **3+** | **13** | **140** | **−$212.40** |

The two worst are unmistakable: `26AUG15CLECHI` — 18 markets held, 46 of 56 trades lost,
**−$45.04** — and `26AUG13GBPIT` — 20 markets, 43 of 71 lost, **−$34.75**. One preseason blowout
resolves a nested, two-sided spread ladder against a seller at a single instant. `mmsell10` alone
held up to **11 markets on one game**; `mmsell5` up to 16.

This is exactly the correlation `mmsell_contest_cap` (XOS-000020, `docs/MMSELL_CORRELATION_CAP.md`)
was built for, and it is **default OFF** — only `Gmmsell1` carries it. So it does **not** protect
the live lineage today.

**What it would and would not do.** Under `contestcap=1` a book holds at most one market per
contest, so `mmsell10`'s 158 KXNFLSPREAD trades become at most 44. At the measured
equal-weighted-by-contest expectation of −10.0¢, that is roughly **−$4.40 instead of −$16.79** —
a ~4× reduction in the damage and a much larger reduction in the variance. It is still negative,
because the cap changes how many positions ride one result, not what one position is worth.

> **The satisfying answer is only half true, and the half it gets wrong is the important half.**
> The contest cap is the right control for the tail and it is not a substitute for the selection
> question.

---

## 5. Is this the §9 mechanism generalising, or is it NFL?

The framing question was whether `docs/MMSELL_ROADMAP.md` §9's in-play head-to-head loss engine
extends to spreads, or whether something is specific to NFL. The sibling table decides it, and
the answer is neither of the two offered:

| series | trades | mkts | contests | P&L | ¢/trade | entry | mkt loss% |
|---|---|---|---|---|---|---|---|
| **KXNFLSPREAD** | 1,554 | 382 | 44 | **−$166.55** | **−10.72** | 9.3¢ | 22.0% |
| **KXNFLTOTAL** | 911 | 301 | 43 | **+$50.32** | **+5.52** | 13.6¢ | 9.0% |
| KXNFLGAME | 100 | 57 | 38 | −$0.59 | −0.59 | 26.8¢ | 33.3% |
| KXNCAAFSPREAD | 929 | 350 | 67 | +$1.18 | +0.13 | 11.6¢ | 10.3% |
| KXMLBSPREAD | 5,147 | 1,418 | 345 | +$127.50 | +2.48 | 12.7¢ | 13.2% |
| KXNCAAFTOTAL | 757 | 293 | 56 | +$18.04 | +2.38 | 12.6¢ | 14.3% |

**KXNFLTOTAL made +$50 on the same 43 games, in the same weeks, in the same books.** So it is not
"NFL preseason is unpriceable" and it is not "spreads are the loss engine" — `KXMLBSPREAD` and
`KXNCAAFSPREAD` are both positive. It is *this contract in this league*, and the two candidate
mechanisms are:

1. **Scoring quantum vs line granularity.** A football spread threshold (3, 7, 10, 14) is crossed
   by a **single scoring play**. That is the §9 mechanism — "a single discrete event that flips
   the outcome" — but attached to a threshold rather than to a winner, which §9 explicitly said
   spreads did not have. In baseball the run line moves one run at a time; in football a 6¢ tail
   on "wins by 14+" while leading by 10 is one touchdown from resolving.
2. **A line built for a game that is not played.** Preseason handicaps are anchored to
   starter-quality expectations, and coaches empty the bench. Consistent with week 3 (+$7.59,
   5.5% trade loss rate) reverting toward normal as rotations settle.

**Neither is established, and they make different forward predictions** — (1) survives into the
regular season, (2) does not. Distinguishing them is the value of the out-of-sample test below,
and it is why acting as though the answer is already known would be the mistake here.

### The line axis: dropped, deliberately

`docs/MMSELL_MARKET_TYPES.md` warns that the ticker-suffix regex is unreliable and that a
per-line table did not survive Bonferroni correction. The durable replacement —
`mmsell_candidate_ticks.floor_strike` / `yes_sub_title` — is forward-only from 2026-08-05, so
coverage over an Aug 13–29 window is partial at best, and a per-line split of 382 markets across
44 games would be a 14-way mine of exactly the kind that doc rules out. **Not pursued.** The
regular-season test below will have both the coverage and a pre-registered hypothesis; that is
when to ask.

---

## 6. Timing — not the explanation

| hold | KXNFLSPREAD ¢/trade | KXNFLTOTAL ¢/trade |
|---|---|---|
| < 1h | +2.28 | +7.69 |
| 1–2h | +5.26 | +5.46 |
| 2–4h | **−15.99** | +2.30 |
| 4–12h | **−18.49** | +3.79 |
| 12–48h | **−14.97** | +10.54 |
| 48h+ | **−11.76** | +11.15 |

KXNFLSPREAD is negative in every bucket beyond two hours and KXNFLTOTAL is positive in all six.
The §9a final-hour effect is visible (the two shortest buckets are the only positive ones) but it
does not separate the two series — TOTAL earns at every horizon. Timing is not what is wrong here.

---

## 7. What is proposed — pre-registered before any counterfactual is scored

> **Nothing in this section has been executed. Research Lab writes the proposal; the transition
> belongs to the owning role.** Adding a series to a running book's universe is a change to its
> selection rule, which under `NEW_ONLY` is a new epoch or Version — never a config edit.

**The in-sample counterfactual is worthless and is not offered.** "Skipping KXNFLSPREAD would
have earned +$166.55" is the definition of the cell we selected on. The only counterfactual worth
anything is out of sample, and the regular season supplies it starting now.

### Proposal 1 — refuse KXNFLSPREAD on the **live mirror only**; leave paper untouched

> **BUILT and SHIPPED — operator-approved 2026-09-06.** `mmsell_live_skip_series`
> (`kalshi_bot/config.py`), enforced by `MmSellTracker._live_paused_blocks` at the same
> call site as the tier bar, on the rule `universe.exposure_paused`. Tests:
> `tests/test_mmsell_live_exposure_pause.py`. Default: `KXNFLSPREAD`. **Paper is
> untouched**, which is what keeps Proposal 2 scoreable. Set the value to `""` to lift.
>
> It is an *interim exposure pause*, not a verdict and not a validated selection rule.
> The permanent answer is Proposal 2's gate, and this pause is what buys the time to
> score it without paying for the wait in real money.

The narrowest intervention that matches both the evidence and the risk. It is exactly the shape
`mmsell_live_min_tier` already implements (`docs/MMSELL_UNIVERSE_REVIEW.md`): it can only ever
*refuse* an entry, so it moves real-money exposure in the safe direction only, and paper keeps
collecting the regular-season evidence at full power. Real money waits; the experiment does not.

*Rationale for the asymmetry:* live downside is unbounded in the season ahead (18 weeks × ~16
games against 44 games of total preseason supply) and the measured live upside on this cell over
its whole history is −$3.71. Paper downside is zero and its information value is the entire test.

*Deliberately NOT a review tier.* It sits beside `mmsell_live_min_tier` rather than inside
it, because the two make different claims and merging them would corrupt both. The tier is
a **governance** rule that makes no claim about returns — `docs/MMSELL_UNIVERSE_REVIEW.md`
is emphatic that conflating the two is how a governance rule quietly becomes an unvalidated
strategy. This bar *is* motivated by returns, so it carries the opposite risk: that a pause
taken on evidence which explicitly could not clear the power bar hardens into a permanent
"we know this loses" nobody re-tests. Leaving paper untouched is the structural answer to
that — the evidence that would lift it keeps accruing whether or not anyone remembers.

*Known limitation, inherited from the tier bar and not introduced here:* a live/paper twin
still opens its paper position on a refused series, so the twin's population is a superset
of live's on these tickers. `_live_tier_blocks` has had this property since PR #338; the
parity read should exclude paused series rather than have this bar silently reach into a
twin, which would be a change to paper.

*Not proposed:* barring paper too. That would make the out-of-sample test impossible and turn a
research finding into a permanent exclusion by construction — the one-way ratchet the universe
tiers are explicitly designed to avoid.

### Proposal 2 — the pre-registered gate (written before any regular-season data was read)

> **`KXNFLSPREAD` regular-season out-of-sample test.** Population: KXNFLSPREAD markets settling
> on or after **2026-09-03** (regular season only; preseason evidence is not poolable with it —
> the roster convention that motivates mechanism (2) is a different world). Unit of observation:
> the **contest**, not the market. Statistic: mean ¢/trade per contest across the paper family,
> cheap band (tail ≤ 7¢), with a bootstrap 95% CI.
>
> * **Sample floor: n ≥ 44 regular-season contests** — matching the preseason sample so the two
>   windows are read at equal power, reached in roughly three NFL weeks.
> * **CONFIRM (the cell is structurally negative; mechanism (1)):** upper bound of the 95% CI
>   **< 0**. Disposition: propose the exclusion as a new Version of the owning experiment.
> * **REFUTE (preseason artefact; mechanism (2)):** lower bound **> −2.0¢**. Disposition: lift
>   Proposal 1's live refusal; record the preseason cell as a season-boundary finding and add the
>   *preseason window*, not the series, to the watch list.
> * **INCONCLUSIVE:** anything else. Extend to n ≥ 88 contests (about six NFL weeks) and re-read
>   **once**. A second inconclusive read resolves as REFUTE — the cell is then not separable from
>   noise at twice the power that found it, and the live refusal is lifted.
>
> Scored by `scripts/mmsell_series_pnl.py --series KXNFLSPREAD --maxyes 7` plus the per-contest
> query recorded in this document. **Not** re-interpreted after the fact; the three branches
> above are exhaustive by construction.

### Proposal 3 — read `mmsell-correlation-cap` on NFL, not on MLB

`Gmmsell0`/`Gmmsell1` (`docs/MMSELL_CORRELATION_CAP.md`) were sized on a baseball drawdown of
−$6.78. The two contests in §4 are a 6× larger instance of the same mechanism, in the sport with
the highest ladder depth per contest we trade. This is a **recommendation to the experiment's
owner about what window to read it over** — it changes no arm, no cap and no default.

---

## 8. Why nobody saw this, and what now watches for it

Nothing in the repo read per-series P&L. `mmsell_market_types` aggregates 400 series into 15
contract types, so a series is never the unit of analysis; `mmsell_universe_review` ranks by
*coverage* and prints P&L only as a footnote — which is, literally, how this was found; and no
gate scores a series at all, correctly, because at this book's variance a per-series P&L gate
would fire constantly on noise.

**`scripts/mmsell_series_pnl.py`** (ops: `{"type":"script","name":"mmsell_series_pnl"}`) is that
missing read: every traded series ranked by realized P&L, with `mkts`, **`contests`**, `edge`
(`be% − loss%`, the only cross-series-comparable column), `worst3%` — of everything a cell lost at
the contest level, the share carried by its three worst contests, which separates §4's
concentration story from a broad drift without anyone writing a query (it reads **47%** for
KXNFLSPREAD; §4's 48%/87% figures are shares of the cell's *net* total, a different and
unbounded denominator that the report deliberately does not use) — and a `live` column naming the real-money books that
touched the cell.

It is a **report, not a gate.** It ranks where to look; it decides nothing. Gates decide
promotions, and a per-series P&L gate on this book is precisely the instrument roadmap §1 says we
do not have the power to build.

Standing use: run it weekly with `--days 7` and again with `--all-time --min-n 50`. The two
disagree exactly when a cell is *new*, which is the case this exists to catch.
