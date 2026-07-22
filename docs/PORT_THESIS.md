# PORT — portfolio composition, correlation & realizable-allocation measurement

*Thesis written 2026-07-22 (idea-model Area 3: portfolio construction), before any validation ran;
the predictions below are pre-registered. Status: **ALLOCATION PREMATURE** (probe run 2026-07-22;
see RESULTS). **Diagnostic, not an edge** — the portfolio-level measurement the "$100/mo from any
combination" north star implies but the bot never built.*

## RESULTS (2026-07-22 probe run — `port_study`, family-clustered v2)

**Verdict: ALLOCATION PREMATURE — the portfolio holds ~1 independent realizable-+EV strategy where
≥2 are needed (P1 FAIL). Not a failure — the expected, valuable measurement.** The realizable
adjustment (fill-model constants) correctly collapsed the mmsell paper mirages to ~0/neg (mmsell3
paper +1.64¢ → realizable **−1.06¢**; mmsell6/mmsell11 → negative) while keeping **mmsell10**
(+1.40¢ realizable, Sharpe **1.48**, $0 max-drawdown) as the one genuine +EV book. Every
realizable-+EV active book (`mmsell`, `mmsell1`, `mmsell2`, `mmsell5`, `mmsell10`) is in the
**mmsell maker-sell family** — one bet on overlapping markets, not five (P3) — and **no non-mmsell
book is realizably +EV** (weather all negative, incl. `weather_con`; theta shelved). Effective
**independent** +EV cluster count = **1** → **P1 FAIL → premature.**

- **A probe bug was caught first (the discipline working, again).** v1 falsely read *P1 PASS (2
  independent +EV clusters)* — an artifact: the realizable scaling **sign-flipped** mirage books
  (corrupting the correlation matrix) and correlation-only clustering **split the mmsell family**
  (`mmsell10`, recent + low-variance, measured < 0.7 vs the older control). v2 fixes both —
  correlations on raw paper series, and same-strategy-family books forced into one cluster
  structurally (the P3 gate). The corrected read is the honest one.
- **Decision (pre-committed):** allocation premature — the binding constraint is **edge supply**,
  not allocation math. Act on **hygiene** (collapse the mmsell variants to `mmsell10`; prune the
  persistently-negative weather cells) and keep `port_study.py` as the **standing portfolio view**
  (loop-checker / full-update) — it re-opens the allocation question automatically the moment a
  2nd independent +EV book lands.

## One-liner

From `paper_trades`, build each book's daily realized-P&L series and answer the question the goal
depends on: **how many genuinely +EV, low-correlation books does the portfolio actually contain,
and does any capital weighting clear a meaningful $/mo at a tolerable drawdown vs today's flat
sizing?** The Phase-1 rollup already suggests the answer — **~1 realizable +EV active book
(`mmsell10`)** — so the expected verdict is **allocation premature**.

## Mechanism (why this is a measurement, not an edge)

- **What's "mispriced":** nothing — this is not a market edge. It is the missing meta-layer: every
  book is judged standalone on per-trade EV; there is no cross-book correlation matrix, no
  capital-allocation math, no netting. The only correlation code in the repo is a one-off
  `tfav`-vs-`theta` pairwise check.
- **Why it matters / who's on the other side:** the north star is explicitly *"$100/month from any
  combination."* A portfolio view tells us (a) whether combining the current books helps, and (b)
  the moment a 2nd +EV book lands, exactly how much it's worth and how to weight it.
- **Honest prior — the layer is premature, not missing.** Allocation and diversification reduce
  *variance*; they **cannot turn a bag of ~0/negative-EV books positive**. They pay off only with
  **≥2 genuinely +EV, uncorrelated books.** Phase-1 reality: `mmsell10` is the one realizable +EV
  active book (+1.40¢ realizable, n=119); the other mmsell variants are fill-model **mirages**
  (realizable ~0/neg — `MMSELL_FILL_MODEL.md` §4); `weather_con` is now net-negative across every
  window; theta is a shelved high-variance flier; the rest are stale/pruned or losers. So the
  expected verdict is **premature**, which is a valuable result — it redirects effort to **edge
  supply**, not weighting.
- **Family:** portfolio-construction — a new *meta* family (a diagnostic layer), distinct from the
  edge families in the scorecard.

## Pre-registered predictions (the gate)

- **P1 — the precondition for allocation exists.** ≥ **2** books have **realizable** mean > 0 at
  **n≥100** AND pairwise daily-return correlation **< ~0.5** (treating the mmsell variants as ONE
  cluster — see P3). **KILL/HOLD if < 2** → allocation is premature; the highest-leverage action
  stays *finding/validating edges*, not weighting them. (Realizable = fill-model-adjusted for the
  mmsell family: only `mmsell9`/`mmsell10` count as +EV there, `MMSELL_FILL_MODEL.md` §4.)
- **P2 — allocation actually helps (only evaluated if P1 passes).** An inverse-variance /
  risk-parity (or realizable-edge-weighted) portfolio beats **flat** sizing on realized $/mo **at
  equal-or-lower max drawdown**, evaluated **out-of-sample** (fit weights on the earlier half of
  the history, measure on the later half). **KILL if flat sizing ties it** (no allocation alpha).
- **P3 — independence, not raw count.** The 11 mmsell variants trade overlapping markets, so they
  collapse to **one** correlated cluster; the "effective independent-book count" is what P1
  measures. Reported explicitly so the portfolio isn't mistaken for having 11 bets when it has ~1.
- **Decision rule (pre-committed):** build a portfolio-allocation layer only if **P1 AND P2**.
  Otherwise: (a) log "allocation premature"; (b) act on the **hygiene** recommendation — collapse
  the mmsell variants to `mmsell10`, prune the persistently-negative weather cells; (c) keep
  `port_study.py` as a **reusable portfolio view** for the loop-checker / full-update, so the
  moment the edge pipeline delivers a 2nd +EV book, the allocation question re-opens automatically.

## Probe plan

- **Script:** `scripts/port_study.py` (allowlisted in `ops_runner.py`). Read-only DB via `psycopg`
  + `DATABASE_URL_RO`, reusing the RO-connection pattern from `scripts/oflow_study.py`.
- **Dataset + provenance:** `paper_trades` (`status='settled'`, `pnl` not null, `legacy=false`),
  aggregated to per-book per-day realized P&L. The realizable adjustment for the mmsell family is a
  **sourced constant** from `MMSELL_FILL_MODEL.md` §4 (only `mmsell9`/`mmsell10` realizably +EV),
  transparently applied — not silently mixed.
- **Measurement:** (1) per-book realized stats — n, total, ¢/trade, daily mean/sd, Sharpe, max
  drawdown, worst day (the `weather_score` risk lens); (2) the cross-book daily-return
  **correlation matrix**; (3) the **effective independent-book count** (correlation-clustered);
  (4) a **flat vs inverse-variance** portfolio comparison — $/mo, Sharpe, max-dd for each.
- **No-lookahead:** P2's flat-vs-weighted comparison fits weights on the earlier time-split and
  evaluates on the later; no future information sizes a past trade.
- **Promotion result:** P1 AND P2 → hand a portfolio-allocation layer to `kalshi-strategy`. Else →
  "premature," logged, hygiene acted on, script kept as the standing portfolio view.

## Cost + capacity

- **Cost:** none directly — this is measurement. (The allocation it would inform still faces each
  book's own fees; the realizable adjustment already nets those.)
- **Capacity:** the reusable view scales with the book roster; the binding input is how many +EV
  books exist, not compute.

## Correlation

- **Vs current book:** this *is* the correlation analysis. Its value is (a) preventing us from
  building an allocation layer with nothing to allocate, and (b) a standing tool that prices the
  next uncorrelated book the instant it lands.
- **Value to $100/mo:** high *leverage* but gated on edge supply — it makes the "$100/mo from any
  combination" goal measurable and tells us the honest truth that the bottleneck is edges, not
  allocation.
