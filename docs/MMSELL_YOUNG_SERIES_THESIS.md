# YOUNG-SERIES — do mmsell fills in series the exchange has only just started listing escape adverse selection?

*Thesis written 2026-09-30, before the probe ran. The predictions, bars and kill criteria are
pre-registered here and are not re-scoped after results. Origin: Research Lab idea-model run
[`IDEA_MODEL_20260930_MMSELL.md`](IDEA_MODEL_20260930_MMSELL.md), operator request (Calvin,
2026-09-29 handoff): "how can the MMSELL book make real money?" Status: **pending probe.** No
probe script exists yet; building it is a `kalshi-probe-builder` step, and its `ops_runner.py`
allowlist entry is the operator's hard stop.*

## One-liner

Rest mmsell NO bids preferentially in series that are **young to the board** — series whose first
in-band market appeared within the last 30 days — on the premise that informed YES takers
concentrate in established series, so the fills we get there are less adversely selected.

## Which bucket it moves, and why the fill does not select against it

The live book's whole paper-to-live gap is *which* orders fill (`mmideas-q1b-0929`,
`mmideas-truth-0930`): the fills earn ~+0.2¢, the rests that never fill would have earned +6.56¢.
This idea is a **universe** lever. It does not try to predict which resting order will fill; it
chooses *where to rest* so that the taker who does hit the bid is more likely the retail longshot
buyer the strategy exists to sell to. It moves the composition of the **filled** bucket and, if the
live tier bar is later relaxed on evidence, the **never-ordered** bucket (the tier bar refuses
series with no review history, which are disproportionately young).

## Where the idea came from (and why that is a weakness)

On 2026-09-29 a post-hoc cut of Fmmsell10's live fills by the series' history in the incumbent
paper book (ops `mmnext-oos-0929`) found:

| series history before 9/7 | fills | ¢/fill |
|---|---|---|
| ≥ 30 trades, was profitable | 309 | −0.98 |
| ≥ 30 trades, was losing | 143 | −0.45 |
| **< 30 trades (thin / new)** | **230** | **+2.28** |

The same read showed series-level past P&L does not predict future fills. The thin-history cell was
the one positive live cell, spotted **after the fact, on the same sample**. THIN-MARKET
(`MMSELL_THIN_MARKET_THESIS.md`) tested a *market-level* translation (24 h traded volume) and was
KILLED: thin markets fill less and worse. That leaves the *series-level* reading untested. This
probe tests it with an outcome-blind, mechanistic feature, first on live books the observation
never touched, then forward on fresh fills.

## Mechanism

Kalshi's informed YES takers (sharps, bots keyed to a game feed) work the series they know. A
series that has just been listed, or a seasonal series in its first weeks, carries launch flow:
retail, novelty, and less algorithmic coverage. A post-only NO bid in such a series is hit by
that flow more often and by an informed taker less often. The prediction is that per-fill realized
P&L is higher in young series than in mature ones, at a similar fill rate.

**Not a duplicate.** Market-level 24 h volume is dead (THIN-MARKET). Series-level past P&L is dead
(`mmnext-oos-0929`). Pre-post taker flow is dead (FLOW-VETO). Spread, depth and outcome count are
dead in the cheap band (`MMSELL_ROADMAP.md`). None of those is series *age*.

**Why it could fail (honest priors):** (a) young series are thin, and THIN-MARKET showed thin fills
are worse, not better; (b) sharps may follow retail into new series within days; (c) the motivating
cell may be one seasonal cohort (NCAAF/NFL season start) rather than a general effect. Three
selection levers on this book were null on 2026-09-29, so the prior is **LOW–MEDIUM**.

## Pre-registered probe

**Samples (provenance: bot DB for orders, fills and series history; Kalshi public REST for
settlement, exactly as `mmsell_thin_market_probe.py`).**

- **Primary (out-of-sample of the observation):** live NO buys of `mmsell10`, `mmsell10a`,
  `mmsell10b`, `Lmmsell10`, `Cmmsell10`, `Dmmsell10`, `Emmsell10` created from
  `2026-07-26T00:00:00Z` up to `2026-09-07T02:03:36Z`. The 9/29 observation was computed on
  Fmmsell10 only, so nothing in this sample informed the idea.
- **Confirmation A (in-sample of the observation, reported, never decisive):** `Fmmsell10` from
  `2026-09-07T02:03:36Z` to `2026-09-30T00:00:00Z`.
- **Confirmation B (forward, decisive):** `Fmmsell10` and any registered successor live tag of the
  same book from `2026-09-30T00:00:00Z` onward. This is the only true out-of-sample test of the
  observation and it accrues at roughly 30 fills a day.

**Feature (frozen): `AGE`.** Days between the order's decision instant and the series' **first
appearance anywhere in the bot's own history**: the earliest `captured_at` in
`mmsell_candidate_ticks` for that `series`, or the earliest `created_at` in `paper_trades` for any
tag whose `market_ticker` starts with the series prefix, whichever is earlier. The series is the
ticker prefix before the first `-`. The decision instant is `execution_order_context.decided_at`,
else `live_orders.created_at`. Only rows strictly before the decision instant count, so nothing
after the decision leaks in. Series already present at the start of the bot's history (June 2026)
are left-censored and read as mature; that is conservative for the hypothesis.

**Split (frozen, absolute, outcome-blind):** **YOUNG** = `AGE ≤ 30` days; **MATURE** = `AGE > 30`.
Bands `≤ 7`, `8–30`, `31–60`, `> 60` are reported for shape only and decide nothing.

**Outcome (real money):** per filled order, `(settle_NO − limit) × min(filled, 1)`, fill time and
quantity read as in `mmsell_flow_veto_probe.py`; settlement from `GET /markets/{ticker}`.

### Predictions, bars and kill criteria

| id | claim | PASS | KILL / HOLD |
|---|---|---|---|
| **Y0** instrument | `AGE` is computable | for ≥ 95% of primary orders | < 95% → **HOLD (instrument)** |
| **Y1** floor | the young cell is readable | ≥ 150 settled YOUNG fills in primary | fewer → **HOLD (accrual)**; trigger below |
| **Y2** primary | young fills are less adversely selected | `mean(YOUNG) − mean(MATURE) ≥ +1.0¢` **and** the bootstrap 5th percentile of the separation is `> 0` | separation `≤ 0` at the Y1 floor → **KILL**; in between → **HOLD (underpowered)** |
| **Y3** forward confirmation (B) | the effect exists on fills the idea never saw | separation `> 0` at `n_young ≥ 100` | separation `≤ 0` at `n_young ≥ 100` → **KILL** |
| **Y4** capacity | the cell can carry a book | YOUNG fill rate ≥ 50% of MATURE fill rate **and** ≥ 5 YOUNG fills/day over the primary window | either fails → **HOLD (capacity)**, even if Y2 passes |

Confirmation A is printed beside Y2 and Y3 but is not a gate: it is the sample the idea was
noticed on.

**Decision rule (pre-committed).**

- **PROMOTE** = Y0, Y1, Y2, Y3 and Y4 all pass. That makes "young series" a candidate **universe
  treatment** for a successor live canary. It does not arm anything: a successor is
  `arm_live_canary` with a fresh tag, a twin and a pre-registered envelope, and it is an operator
  hard stop.
- **KILL** on Y2 or Y3 closes universe selection on this book: series age joins series P&L
  (`mmnext-oos-0929`) and market volume (THIN-MARKET) as null, and the run doc's recommendation
  becomes "stop investing research effort in MMSELL as a P&L line."
- **HOLD** on Y1 means the primary sample lacks young fills; the trigger is Confirmation B reaching
  150 young fills (about 3–5 weeks at current flow), after which Y2 is evaluated on B and Y3 on the
  half of B that accrued after that evaluation date, in that order, so no fill is scored twice.

**What is reported alongside (never decisive):** fills/day in the young cell; implied $/month at
`size = 1` and `size = 3` (`fills/day × separation × 30`); the KXRAIN and other scheduled non-crypto
cells as their own rows, because `mmideas-q1b-0929` showed KXRAIN filled +6.54¢ at n=17 and that
cell is otherwise untracked.

## Cost + capacity

- **Fees:** maker fills bill ~0.01¢/contract (`MMSELL_FEE_RECON.md`). No fee correction is owed.
- **Adverse selection:** the whole point; measured directly on real fills, not haircut from paper.
- **Capacity:** the young cell was 230 of 682 live fills over three weeks on Fmmsell10. If the
  separation holds at +1–2¢, that is roughly 10 fills/day × 1.5¢ ≈ **$4–5/month at size 1**, and
  **$13–15/month at size 3** if the cell-size census (`MMSELL_CELL_SIZE_CENSUS.md`) finds a
  fill-neutral price cell. Stated up front: even a PASS does not reach the $100/month north star on
  its own. It is the only cell on this book with a positive live number, which is why it is tested
  before the book is written off, not because it is large.

## Correlation

Same return driver as the live book (favorite-longshot maker-sell on sports cheap tails). This is
a refinement of an existing book, not new ballast; it earns no diversification credit and is
screened on that basis in the run doc.

## RESULTS

*(empty until the probe runs; the verdict is recorded here, in `RESEARCH_JOURNAL.md` and in the
scorecard row, and the pre-registered bars above are not edited)*
