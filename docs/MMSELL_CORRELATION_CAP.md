# mmsell contest cap — the experiment · `Gmmsell0` / `Gmmsell1`

**Pre-registered 2026-09-05. Paper only. Ticket: XOS-000020 (HIGH/P1, STRATEGY, RESEARCH_LAB,
scope `mmsell-price-ceiling-capacity`).**

Contract: `kalshi_bot/experiment_os/correlation_cap.py` (experiment `mmsell-correlation-cap`,
package `mmsell-correlation-cap`). **Mechanism: already merged** as `576bcfa` —
`regimes.contest_key_of` and `mmsell_contest_cap`, default OFF. This document covers the
per-book override that makes it measurable, the two arms, and the gate. Metrics:
`daily_pnl_stability`, `settled_days`. Tests: `tests/test_mmsell_contest_cap_per_book.py`,
`tests/test_correlation_cap_package.py`, `tests/test_experiment_os_daily_shape_metrics.py`.

> ## This document does NOT own the mechanism.
>
> `576bcfa` (session `01Ar3bKM…`, merged 2026-09-05T12:32Z) built the contest cap independently
> and concurrently with this work, from the same ticket and the same live slate. It ships the
> right mechanism and it ships it **off**, because `mmsell_contest_cap_enabled` is global and
> `tracker.py` is shared — a shared-semantic change it correctly routed to Platform Change
> Review rather than merging switched on.
>
> That global scope is also why it cannot be measured as it stands: switching it on caps every
> mmsell book at the same instant, so there is no window in which a capped book and an uncapped
> control run side by side, and the only available comparison is before-versus-after across two
> different market regimes. This adds the per-book `contestcap=` override its own commit
> anticipates ("a book opts in through its own registered risk envelope"), two fresh tags, and a
> pre-registered gate. **The global default stays off and no other book's selection moves.**

> ## READ THIS FIRST — the axis the ticket named is the half that does nothing.
>
> XOS-000020 diagnosed the `Dmmsell10` failure as **cross-series contest clustering**: one MLB
> game held under five separate series, five "events" that one high-scoring night resolves
> together. That is correct about the live tape, and it is **not** where the money is. The
> merged mechanism was sized on those same 97 live fills, all from one MLB slate.
>
> Replayed instead over `mmsell10`'s **3,296 settled paper trades across 35 settlement days**,
> the ordering holds but the attribution does not:
>
> - capping ONLY the cross-series contest — the sports grouping the mechanism was built for —
>   is worth **+0.09¢/trade and does not improve the worst day at all** (−$9.49 → −$9.56), and
>   is **worse than its own control risk-adjusted** (0.218 → 0.153);
> - capping every unit of correlation at 1 is worth **+0.61¢/trade and cuts the worst day 76%**
>   (→ −$2.31), with daily volatility down 48%.
>
> `contest_key_of` **delivers the second** — outside `CONTEST_GROUPED_REGIMES` it falls back to
> the event ticker, so `cap=1` tightens every ladder from the rung cap's 3 rungs to 1. So the
> merged knob is right, and the thing it was named for is the part carrying almost none of the
> effect. The fallback its author treated as a conservative default is where the value is.
>
> Recorded up front, and as evidence on XOS-000020, because the next person to tune this cap
> will otherwise tune the grouping and conclude the thesis is dead.

## The problem

Every concentration cap mmsell has ever run keys on `event_ticker`, which is **series ×
occasion**, not occasion. Measured on the live book on 26SEP02: 31 markets across 23 distinct
event_tickers spanning **eleven real games**. NYYLAA alone carried positions under
`KXMLBF5TOTAL`, `KXMLBHR`, `KXMLBSPREAD`, `KXMLBTEAMTOTAL` and `KXMLBTOTAL`. The rung cap permits
3 per event_ticker, so one game can legally hold ~15 correlated positions and no cap notices.
That slate was the entire live drawdown (−$6.44 gross of −$4.46 net): 10 of 10 losing markets
were MLB, 9 on that one slate, while 33 settled non-MLB markets lost nothing.

Independently, on the paper tape, **the largest single unit of correlation ever held is 17
positions**. The book's risk model is diversification. Seventeen rungs on one occasion is not
diversification wearing a different hat; it is one position at 17× size.

## Why the obvious key is wrong (and why the merged one is not)

The first counterfactual keyed on "strip the series prefix off `event_ticker`". It reports
+0.649¢ → +1.071¢/trade, and it is **not interpretable**, because that key groups three
different things at once:

| key | trades | distinct series | P&L | what it is |
|---|---|---|---|---|
| `26SEP022210STLLAD` | 12 | 6 | −$5.18 | a real game ✅ |
| `26AUG1717` | 27 | 8 | −$0.26 | date+hour: unrelated crypto/econ strikes ❌ |
| `26AUG` | 13 | 7 | −$1.12 | a bare **month** ❌ |

So its headline number is a game cap and an accidental *day* cap, blended. It also merges
`KXBTCD` with `KXETHD` at the same hour — different underlyings, one key.

The correct key is not a string transform, because the unit of correlation is a property of how
the contract **settles**, which `market_types.classify` already answers:

| settle mode | unit of correlation | key |
|---|---|---|
| `in_play` | the CONTEST, shared across series | the contest token off the event ticker |
| `scheduled` | one underlying at one instant | the event ticker (so BTC ≠ ETH) |
| `discrete` | the window itself | the event ticker |
| unclassified | itself, never merged with anything | the event ticker |

For `scheduled`/`discrete` this changes *what groups* not at all — only the cap applied to it.
Only `in_play` gets a coarser key than mmsell has ever used. That asymmetry is why there are two
books rather than one.

## The two books

Both use the same base (`lo=5,hi=10,maxyes=7`), so entry is held constant and they differ by
exactly one thing. Neither carries a stop, vol gate or strangle leg — those are the anchor set's
experiment and would confound this one.

| tag | arm | spec | |
|---|---|---|---|
| `Gmmsell0` | control | `lo=5,hi=10,maxyes=7` | every existing cap applies exactly as today |
| `Gmmsell1` | treatment | `…,contestcap=1` | one open position per contest, via `contest_key_of` |

Two arms rather than three: the contest-grouping and event-tightening halves are **not**
separable through the merged key — `contest_key_of` does both at `cap=1` by construction, and
splitting them would mean forking a key that is already merged and tested. The decomposition
above is therefore recorded as the counterfactual's finding rather than built as a third book,
which is the cheaper way to carry the same information.

**Why the control is `Gmmsell0` and not `mmsell10`.** `mmsell10` is already the control arm of
`mmsell-price-ceiling-capacity` v3/e2, and a tag carries one active deployment arm — claiming it
would mean ending a running experiment's deployment to start this one. Naming it as an
**external** control in the gate instead is precisely what has `mmsell-anchor-vol-entry` sitting
in `BLOCKED_PLATFORM`: *"external control … is pinned to a different platform snapshot — a
cross-snapshot delta would pool incomparable evidence."* An in-experiment control shares this
epoch and snapshot by construction and cannot acquire that failure mode. Its `n` restarts at 0,
which is correct anyway: all three arms have to be read over the same window.

## Counterfactual on the paper tape (n=3,296 · 35 settlement dates · post-cohort)

| rule | trades | total | ¢/trade | worst day | p5 day | daily sd | daily mean/sd |
|---|---|---|---|---|---|---|---|
| control (`mmsell10`) | 3,296 | +$21.39 | +0.649 | −$9.49 | −$4.03 | 2.803 | 0.218 |
| contest grouping only | 1,707 | +$12.61 | +0.739 | **−$9.56** | −$3.28 | 2.361 | **0.153** |
| cap 1 per contest (`Gmmsell1`) | 1,326 | +$16.69 | **+1.259** | **−$2.31** | −$2.26 | **1.465** | **0.326** |

**Read the ¢/trade column carefully — it is not a claim that the cap makes money.** Both caps
decline trade sets that were *profitable on average*: +0.239¢/trade over 1,970 declined trades
(all-scope), +0.553¢ over 1,589 (game-scope). Total dollars **fall** in both. ¢/trade rises
because the kept trades are better, which is a **capacity** statement, not a P&L one: it converts
into dollars only if the freed slot is reused, which paper (200 open positions, never binding)
cannot demonstrate and live (20 open positions, always binding) would.

So `Gmmsell1` is, on this evidence, a **drawdown control that costs 22% of total return to cut
worst-day loss by 76% and daily volatility by 48%** — while the grouping half on its own costs
41% of total return and cuts neither. That is what the gate below has to test.

## Pre-registered gate — `correlation_cap_keep`

> ### Why this is NOT gated on ¢/trade, despite ¢/trade being the headline number
>
> Measured per-trade standard deviation on this tape is **$0.2343**. Detecting a +0.30¢
> difference between two books at 80% power, α=0.05, needs
> `n = 2σ²(z_{0.975}+z_{0.80})²/Δ² ≈ 95,700 settled trades per arm` — about **2.8 years** at the
> observed ~94 trades/day. A ¢/trade promotion criterion at this resolution is unreachable, and
> pre-registering one would mean a gate that can only ever be satisfied by noise.
>
> This is `docs/MMSELL_ROADMAP.md` §1 ("we are out of statistical power, not out of ideas")
> binding exactly where it predicted. The variance statistics are enormously better powered on
> the same tape, so they carry the verdict and ¢/trade is demoted to a floor.

**Sample floor: ≥ 60 shared settlement dates** with both arms and the control live. Sixty, not
the 35 observed: a 5th percentile over 35 days interpolates between the 2nd and 3rd worst days,
so one bad slate moves it bodily.

Registered as gate `paper_keep` on `mmsell-correlation-cap` v1, in
`kalshi_bot/experiment_os/correlation_cap.py`, and read on `Gmmsell1` against `Gmmsell0`:

- **PASS** at `settled_days ≥ 60` on both arms only if **both** hold:
  1. `delta.daily_pnl_stability ≥ +0.05` — mean(daily P&L)/sd(daily P&L), treatment minus
     control. An **absolute** bar on a scale-free statistic, so it cannot drift with the
     control. The counterfactual read control 0.218 and `Gmmsell1` 0.326, so +0.05 is roughly
     half the observed effect: a bar the arm must clear, not one fitted to what it scored.
  2. `delta.pnl_cents_per_trade ≥ −0.5¢` (a floor, never a promotion criterion — see above).
- **KILL** if `delta.daily_pnl_stability < 0` at n ≥ 60 dates — on this evidence that kills the
  *mechanic*, not the arm: if capping the contest does not steady the daily series, the
  clustering the whole thesis rests on was luck. Also kill below the ¢/trade floor.
- **HOLD** while the mechanism has not fired: `skipped_contest_cap == 0`, or the control never
  holds >1 position on one contest. An arm that declined nothing measured nothing, and its
  numbers are the control's with a different tag.

**There is deliberately no promotion gate.** `Dmmsell10` is stood down and nothing here is a
live candidate; registering a `PAPER → LIVE_CANARY` gate now would pre-authorize a transition no
evidence supports. A promotion is a new gate on a new Version.

**The ratio, not the raw standard deviation, deliberately.** A capped book takes 60% fewer
trades, and *any* book that trades less has lower daily variance. Gating on raw sd would pay the
cap for doing nothing but trading less. `mean/sd` is scale-free in the direction that matters:
control 0.218, grouping-only 0.153, `Gmmsell1` 0.326 — and note it is the one statistic on which
the grouping half reads **worse than its control**, which the raw sd column hides.

## Caveats that bind any reading of this

1. **The counterfactual is post-hoc, on the same tape that generated the hypothesis.** It sets
   the prior; it is not the result. XOS-000020 says this in its own words: *"a POST-HOC slice
   [that] must not become a stopping criterion for the running contract; it is a pre-registered
   hypothesis for the next one."*
2. **Paper, fill-everything.** No maker adverse-selection haircut. Some declined rungs would
   never have filled live, which flatters the cap's cost and its benefit alike.
3. **The counterfactual cannot model reallocation.** It deletes trades; a real capped book frees
   a slot. Live, where slots bind, that is the entire economic case and it is unmeasured here.
4. **35 days is one regime.** `docs/MMSELL_SEASONAL_FORECAST.md`: our whole history cannot speak
   to the Sept–Nov change. The 60-date floor deliberately runs into it.
5. **This does not license re-arming anything live.** `Dmmsell10` is stood down; re-arming is an
   operator act through `service.arm_live_canary`.

## Correction, 2026-09-05 — the contest read is not settlement-date scoped

As merged (`576bcfa`), the contest counter came from the settlement-date query the date and
event caps share: a book's open positions filtered to the CANDIDATE's own UTC calendar date. A
contest is one result, not one date. An MLB F5 total closes ~1h into a game and the full-game
total ~3h, so a first pitch around 18:30 ET or later puts the early legs before UTC midnight and
the late ones after. Those legs then counted against two different days' budgets and the cap did
not fire — on exactly the late-evening games XOS-000020's own drawdown came from (`26SEP02
NYYLAA` was a 21:38 start).

The failure was silent: `skipped_contest_cap` simply did not increment, which reads as "the cap
had nothing to refuse" rather than "the cap is broken" — and `skipped_contest_cap == 0` is the
gate's own HOLD condition, so a broken cap and a cap with nothing to do were indistinguishable.

Fixed by giving the contest cap its own read (`repo.open_positions_contest_summary`) over the
book's WHOLE open book, keyed by `contest_key_of`. The date, correlated-event and rung caps are
untouched and stay date-scoped, which is what they actually model. The read is bounded by
`mmsell_max_open_positions` and only issued for a book that names a `contestcap`, so the
uncapped cohort is unchanged down to the query count.

Landed while both arms had **0 settled trades** (verified against `paper_trades` before the
change), so no epoch is broken and no evidence is discarded.

**Open question for the operator.** The counterfactual above was computed per settlement date on
the same tape. If its grouping carried the same date scoping, it *understated* the cross-series
contest arm — the straddling games it could not group are the late-evening ones. The pre-
registered gate stands as written; whether the prior in the "READ THIS FIRST" box should be
re-derived is a separate call.

## Not built here

The second half of XOS-000020 — `mmsell_settlement_correlated_regimes` defaulting to
`"Elections"` alone, so the 5-events-per-date cap has never once fired for MLB — is a **separate
change under its own epoch**, deliberately. Bundling a config gap into a mechanic's treatment is
how an arm stops isolating one variable.
