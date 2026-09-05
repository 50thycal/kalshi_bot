# mmsell correlation cap — `Gmmsell0` / `Gmmsell1` / `Gmmsell2`

**Pre-registered 2026-09-05. Paper only. Ticket: XOS-000020 (HIGH/P1, STRATEGY, RESEARCH_LAB,
scope `mmsell-price-ceiling-capacity`).**

Contract: `kalshi_bot/experiment_os/correlation_cap.py` (experiment `mmsell-correlation-cap`,
package `mmsell-correlation-cap`). Mechanism: `kalshi_bot/mmsell/correlation.py`,
`MmSellTracker._correlation_cap_blocks`, `repository.open_positions_correlation_rows`. Metrics:
`daily_pnl_stability`, `settled_days`. Tests: `tests/test_mmsell_correlation_cap.py`,
`tests/test_correlation_cap_package.py`, `tests/test_experiment_os_daily_shape_metrics.py`.

> ## READ THIS FIRST — the headline hypothesis was FALSIFIED before either book was built.
>
> XOS-000020 diagnosed the `Dmmsell10` live failure as **cross-series game clustering**: one MLB
> game held under five separate series, so five "events" that one high-scoring night resolves
> together. That diagnosis is correct about the live tape and it is **not** where the money is.
>
> Decomposed on the paper `mmsell10` tape (n=3,296 settled, 35 settlement dates, post-cohort
> boundary), capping the GAME buys **+0.09¢/trade and does not improve the worst day at all**
> (−$9.49 → −$9.56). Capping every unit of correlation buys **+0.61¢/trade and cuts the worst
> day by 76%** (−$9.49 → −$2.31). The effect is almost entirely the **ladder** axis —
> `scheduled`/`discrete` rungs within one event, tightened from the rung cap's 3 to 1 — not the
> contest axis the ticket named.
>
> This is recorded up front because the obvious next reader of XOS-000020 will otherwise build
> the game cap, measure nothing, and conclude the correlation thesis is dead. The thesis is
> alive; the ticket named the wrong axis, on 97 live fills that all landed on one MLB slate.

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

## Why the obvious key is wrong

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

## The three books

All three use the same base (`lo=5,hi=10,maxyes=7`), so entry is held constant and each varies
exactly one thing. None carries a stop, vol gate or strangle leg — those are the anchor set's
experiment and would confound this one.

| tag | arm | rule | isolates |
|---|---|---|---|
| `Gmmsell0` | control | no cap | the baseline; every existing cap applies exactly as today |
| `Gmmsell1` | treatment | `corrcap=1, corrscope=game` | the CONTEST axis alone; every ladder cap untouched |
| `Gmmsell2` | treatment | `corrcap=1, corrscope=all` | every unit of correlation, i.e. also 3 rungs → 1 |

`Gmmsell2 − Gmmsell1` **is** the ladder axis. Running only the tradeable rule would have left
the two inseparable, which is the mistake the naive key already made once.

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
| `Gmmsell1` (game) | 1,707 | +$12.61 | +0.739 | **−$9.56** | −$3.28 | 2.361 | **0.153** |
| `Gmmsell2` (all) | 1,326 | +$16.69 | **+1.259** | **−$2.31** | −$2.26 | **1.465** | **0.326** |

**Read the ¢/trade column carefully — it is not a claim that the cap makes money.** Both caps
decline trade sets that were *profitable on average*: +0.239¢/trade over 1,970 declined trades
(all-scope), +0.553¢ over 1,589 (game-scope). Total dollars **fall** in both. ¢/trade rises
because the kept trades are better, which is a **capacity** statement, not a P&L one: it converts
into dollars only if the freed slot is reused, which paper (200 open positions, never binding)
cannot demonstrate and live (20 open positions, always binding) would.

So `Gmmsell2` is, on this evidence, a **drawdown control that costs 22% of total return to cut
worst-day loss by 76% and daily volatility by 48%** — and `Gmmsell1` is a drawdown control that
costs 41% of total return and cuts neither. That is what the gate below has to test.

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
`kalshi_bot/experiment_os/correlation_cap.py`, and read on `Gmmsell2` against `Gmmsell0`:

- **PASS** at `settled_days ≥ 60` on both arms only if **both** hold:
  1. `delta.daily_pnl_stability ≥ +0.05` — mean(daily P&L)/sd(daily P&L), treatment minus
     control. An **absolute** bar on a scale-free statistic, so it cannot drift with the
     control. The counterfactual read control 0.218 and `Gmmsell2` 0.326, so +0.05 is roughly
     half the observed effect: a bar the arm must clear, not one fitted to what it scored.
  2. `delta.pnl_cents_per_trade ≥ −0.5¢` (a floor, never a promotion criterion — see above).
- **KILL** if `delta.daily_pnl_stability < 0` at n ≥ 60 dates — on this evidence that kills the
  *mechanic*, not the arm: if capping every unit of correlation does not steady the daily
  series, the clustering the whole thesis rests on was luck. Also kill below the ¢/trade floor.
- **HOLD** while the mechanism has not fired: `skipped_correlation_cap == 0`, or the control
  never holds >1 position in one key. An arm that declined nothing measured nothing, and its
  numbers are the control's with a different tag.

**There is deliberately no promotion gate.** `Dmmsell10` is stood down and nothing here is a
live candidate; registering a `PAPER → LIVE_CANARY` gate now would pre-authorize a transition no
evidence supports. A promotion is a new gate on a new Version.

**The ratio, not the raw standard deviation, deliberately.** A capped book takes 60% fewer
trades, and *any* book that trades less has lower daily variance. Gating on raw sd would pay the
cap for doing nothing but trading less. `mean/sd` is scale-free in the direction that matters:
control 0.218, `Gmmsell1` 0.153, `Gmmsell2` 0.326 — and note it is the one statistic on which
`Gmmsell1` is **worse than its control**, which the raw sd column hides.

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

## Not built here

The second half of XOS-000020 — `mmsell_settlement_correlated_regimes` defaulting to
`"Elections"` alone, so the 5-events-per-date cap has never once fired for MLB — is a **separate
change under its own epoch**, deliberately. Bundling a config gap into a mechanic's treatment is
how an arm stops isolating one variable.
