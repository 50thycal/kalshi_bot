# Backtesting, Sizing & Risk

The evaluation and capital machinery — applies to any thesis, not just weather. Read in Phases 4–6.

## Contents
- Backtest methodology (validity first)
- Calibration testing
- Fractional-Kelly sizing (math + caps)
- Paper-trading protocol
- Paper → live promotion gates
- Risk controls

---

## Backtest methodology — validity before performance

A backtest exists to answer one question honestly: *would this edge have survived real conditions?* Optimize for not fooling yourself.

**Point-in-time correctness (the backbone).** Every input must reflect only what was knowable at the decision timestamp. The three classic leaks:
- Using a **settled/revised value** (the final temperature, a revised CPI) that wasn't available when the trade was made.
- Using a **later model run** than the one available at decision time (score the 06Z decision against 06Z data, not 18Z).
- **Survivorship / selection** — silently dropping markets that don't fit, or only testing on a favorable slice.

**Realistic execution:**
- **Fills:** assume you **cross to the opposing bid** (pay up), not mid or best bid. In thin books, model **partial fills** — you often can't get full size at the top level.
- **Fees:** apply the exact per-contract fee on **both** entry and exit. On Kalshi it's quadratic (`≈ 0.07 × price × (1−price)`), worst near $0.50. An edge that's positive gross but negative net is not an edge.
- **Latency:** if the live bot will act on data that arrives with delay, reflect that delay in the backtest (don't assume instant reaction to a model release).

**Out-of-sample discipline:** hold out time periods; tune on train, report on test. Beware regime effects (summer/winter, high/low-vol periods) — test within regime and across regimes separately. If you tuned N knobs on the data you're reporting on, the result is optimistic by construction.

**Report:** net expectancy per trade (after costs), hit rate **by probability bucket**, calibration, and drawdown — not a single gross-P&L number on a cherry-picked window.

**When history is thin:** some market types (one-off political/economic events) have too little usable history to backtest meaningfully. Say so explicitly rather than manufacturing a fragile backtest, and compensate with a longer, more rigorous paper-trading phase.

---

## Calibration testing (do this every iteration)

The single most diagnostic check for a probability-based strategy.

1. Bucket all predictions by predicted probability (e.g. 0–10%, 10–20%, …).
2. For each bucket, compute the **realized frequency** of YES.
3. Plot predicted vs realized (a **reliability curve**). Perfect calibration = the diagonal.

- Predictions above the diagonal = **overconfident** (you say 80%, it happens 65%) → you'll overbet and lose. Widen your distribution / shrink toward the market.
- Well-calibrated but not sharp is fine and tradeable. **Prefer calibration over sharpness** — an overconfident sharp model is worse than a humble calibrated one.
- A profitable-looking backtest with a badly miscalibrated curve is almost always **overfit or leaking** — investigate before trusting it.

Metrics worth tracking alongside the curve: **Brier score** (mean squared error of probabilities; lower is better) and **log loss** (punishes confident wrong calls harder). Use them to compare model versions objectively.

---

## Fractional-Kelly sizing

Kelly maximizes long-run growth *given the true edge*. But your edge estimate is itself uncertain, and **full Kelly is brutally over-aggressive** when the probability is even slightly off — it produces gut-wrenching drawdowns and blows up on a run of wrong estimates. Use a **fraction of Kelly**.

**Binary-contract Kelly.** For a YES contract bought at price `p` (cost `p`, payout $1, so net odds `b = (1−p)/p`), with your estimated win probability `q` (and `1−q` loss):

```
kelly_fraction f* = q − (1 − q) / b
                  = q − (1 − q) · p / (1 − p)
                  = (q − p) / (1 − p)
```

- `q − p` is your **edge** (your prob minus market price). If `q ≤ p`, `f* ≤ 0` → **don't trade** (no edge after the market price; and remember to also subtract fees before deciding).
- **Bet a fraction of `f*`**, typically **¼ to ½ Kelly**. Quarter-Kelly captures most of the growth with far less drawdown and is much more forgiving of estimation error — the right default when your `q` comes from a model you're still validating.

**Adjust `q` for fees first.** Compute edge net of the entry+exit fee, then size. Sizing on gross edge overbets.

**Hard caps (independent of Kelly):**
- **Per-position cap** — max % of bankroll in any single contract/market (e.g. a few %), so one bad settlement can't cripple you.
- **Per-day / per-event cap** — limit total exposure across correlated markets (all of one city's brackets on one day are highly correlated — they're the *same* underlying outcome; size the *event*, not each bracket independently).
- **Bankroll floor / drawdown circuit-breaker** — pause and re-evaluate if the bankroll draws down past a set threshold; a sustained drawdown usually means the model drifted or an assumption broke.

**Correlation caveat:** Kelly assumes independent bets. Brackets within one event are near-perfectly dependent; days can correlate too (a heat wave moves many cities together). Size at the level of the independent bet (the event/day), not the individual contract, or you'll massively overbet.

---

## Paper-trading protocol (Phase 5)

Run the **real** strategy against Kalshi's **demo environment** (`external-api.demo.kalshi.co`, separate keys, fake money) on **live** markets. This surfaces what backtests can't: live data latency, real fill quality, model drift, operational bugs.

- Log **every decision** with full context: input data + its timestamp, your `q`, market price, computed edge, size, the order, and the actual fill. Paper results are only useful if you can diagnose *why* each trade happened.
- Track the **same metrics as the backtest** (net expectancy, calibration, drawdown) so the two are directly comparable — the whole point is the comparison.
- Run long enough to cover **multiple regimes / conditions**, not just a calm week.
- Watch for: paper P&L diverging from backtest expectation; fills materially worse than modeled; signal data arriving later than assumed; microstructure surprises (thin books, wide spreads, settlement timing).

**Divergence is information.** If paper ≠ backtest, do not proceed — diagnose. It's usually fills, latency, or an offline data assumption that was wrong. Fixing it *is* the value of this phase.

---

## Paper → live promotion gates (decide these in advance)

Do not eyeball the go/no-go. Set criteria before you start paper trading, and only promote when all are met:
- **Minimum paper duration** covering multiple market regimes/conditions.
- **Calibration holds live** (reliability curve still near-diagonal on paper data, not just backtest).
- **Net edge survives** realistic costs on paper, consistent with backtest.
- **No unresolved divergence** between paper and backtest behavior.
- **Operational readiness** — kill switch, monitoring, and deploy verification wired in (use `bot-investigation` / `deploy-check`).

Then, going live:
- **Start tiny** — first live size small enough that being wrong about live-vs-paper differences is cheap tuition. Real money behaves differently from demo (liquidity, slippage, psychology of a running system).
- **Scale gradually**, only as live results confirm paper results. Ratchet size up on evidence, down on drawdown.

---

## Risk controls (always on)

- **Demo before production** for any new or materially-changed strategy — no exceptions.
- **Costs are part of edge** — evaluate everything net of fees and realistic fills.
- **No lookahead** — point-in-time correctness underpins every valid backtest.
- **Fractional Kelly + hard caps** — bankroll survival over single-trade upside.
- **Size the independent bet**, not correlated legs of the same outcome.
- **Circuit-breaker on drawdown** — pause and diagnose rather than "trade through it."
- **Credentials manual** — never automate account creation, password entry, or fund movement; RSA key in the secret store, never the repo, unrecoverable if lost.
- **Not investment advice; no guarantee.** Backtested and paper performance do not guarantee live results. Enter live small and monitored.
