---
name: kalshi-strategy
description: |
  Research, design, build, backtest, paper-trade, and iterate a profitable Kalshi (CFTC-regulated event-contract) trading strategy end to end. Use whenever the user wants to build, design, improve, or reason about a Kalshi trading bot or strategy, form a trading thesis for event/prediction markets, wire up market or forecast data for Kalshi, backtest or paper-trade a Kalshi approach, or judge whether an edge on Kalshi is real. Trigger on phrases like "Kalshi strategy", "prediction market bot", "event contract trading", "weather market bot", "trade temperature markets", "backtest this Kalshi idea", or "paper trade on Kalshi". Also trigger when the user references KXHIGH/KXLOW/KXFED-style tickers, NWS/NBM weather data for trading, or asks where the edge is in prediction markets. Treat the work as building the user's OWN trading system — not limited to existing strategies; propose a completely new approach when the research supports it.
---

# Kalshi Strategy Builder

A systems-thinking workflow for taking a Kalshi trading idea from blank page to a paper-traded, evidence-backed strategy — then, cautiously, to live. Kalshi is the first CFTC-regulated event-contract exchange in the US: binary contracts that settle to $1.00 (YES) or $0.00 (NO). The API is REST + WebSocket, prices are dollar-strings 0.00–1.00, and YES + NO always sum to $1.00.

**Framing, not financial advice.** This is an engineering and research framework. It does not promise profit, and it deliberately routes every idea through backtesting and demo/paper trading before real capital, with conservative sizing. Treat backtest results as hypotheses, not guarantees, and enter live trading small.

**The core loop is ordered and gated.** Each phase has an exit gate. Do not skip ahead — the discipline is the point. A strategy that fails a gate goes back a phase, not forward.

```
Phase 0  Inventory existing infrastructure   → gate: know what to reuse vs build
Phase 1  Research + form a falsifiable thesis → gate: edge stated in one sentence
Phase 2  Build the data pipeline             → gate: point-in-time-correct dataset
Phase 3  Implement the strategy              → gate: signal → prob → edge → size → order
Phase 4  Backtest (where history exists)      → gate: positive edge AFTER costs
Phase 5  Paper trade (Kalshi demo env)        → gate: live behavior matches backtest
Phase 6  Iterate, then consider live          → gate: promotion criteria met, size tiny
```

---

## Phase 0 — Inventory the existing system first

Do NOT greenfield. The user already runs trading bots (Railway-hosted, Solana/Helius data, existing execution and logging patterns). Before writing anything, map what exists so the Kalshi strategy reuses it instead of duplicating it. Read the actual repos and configs — don't assume.

Inventory these, from code and deployment config:
- **Data access** — existing market/price feeds, schedulers, caches, any weather or macro data already pulled.
- **Strategy module pattern** — how existing strategies are structured (signal → decision → sizing → execution). Match it so the new strategy is a sibling, not a foreign object.
- **Backtest harness** — is there one? What format does it expect (bars, ticks, event logs)? Reuse it.
- **Paper-trading / dry-run mode** — does the execution layer already support a no-op / simulated mode?
- **Execution + order plumbing** — order construction, retry/backoff, idempotency, position tracking.
- **Logging + observability** — where logs go (Railway), how they're queried, existing dashboards. The `bot-investigation` and `deploy-check` skills describe these; lean on them.
- **Secrets management** — how API keys are stored and injected. Kalshi needs an RSA private key (see `references/kalshi-api.md`); it must live in the same secret store as existing keys, never in the repo.

**Gate:** You can state, in a sentence each, what you will reuse and what genuinely must be built new. If everything is "build new," you haven't read the existing code carefully enough.

---

## Phase 1 — Research the markets and commit to a falsifiable thesis

This is where most prediction-market bots quietly fail: they start from "trade Kalshi" instead of "here is a specific, persistent mispricing and here is why it exists." Fix that here.

**Survey the market families and their edge profile.** Read `references/market-edge-map.md`. It maps Kalshi's categories (weather, economics/rates, crypto price, sports, politics, company/tech) and gives an honest assessment of where a solo quant realistically has edge versus where you are trading against desks, insiders, or an already-efficient crowd. The short version of the key empirical fact: Kalshi's markets are well-calibrated in the tails (contracts priced 90%+ resolve YES ~98–99% of the time), so **edge does not live in the obvious extremes** — it lives in mid-probability brackets, in the time dimension (how fast a market converges), and in categories where you can source or compute better information than the marginal trader.

**Form the thesis.** Write it down explicitly, covering:
1. **Market family + specific contract type** (e.g. daily high-temperature bracket markets, KXHIGH*).
2. **The edge, in one sentence** — what mispricing, and *why does it exist and persist*? "The market underweights X" is only credible if you can name who is on the other side and why they haven't corrected it.
3. **Signal source** — the data or computation that produces your probability estimate. It must be obtainable point-in-time (Phase 2).
4. **Edge magnitude + capacity** — rough expected edge per trade after fees, and how much size the market can absorb (thin markets cap real-world returns regardless of hit rate).
5. **What would falsify it** — the observation that would make you abandon this thesis. If you can't name one, the thesis isn't falsifiable yet.

**The current best-supported candidate** (from prior research and the edge map) is **weather / daily-temperature markets**: NOAA's National Blend of Models publishes *probabilistic* max/min temperature guidance that maps almost directly onto Kalshi's temperature brackets, giving a principled probability estimate that many discretionary traders don't use rigorously. If pursuing this, read `references/weather-strategy.md` in full. But this is a worked example, not a mandate — if the research points elsewhere, follow it.

**Gate:** The edge is stated in one sentence, you can name the counterparty you're beating, and you've named what would falsify the thesis. Vague theses do not pass.

---

## Phase 2 — Build the data collection pipeline

The strategy is only as good as its inputs, and backtests are only valid if the data is **point-in-time correct** — every value must reflect what was actually knowable at that timestamp. Lookahead leakage (using a settled temperature, a revised figure, or a later model run than would have been available) is the single most common way a backtest lies.

You need three data streams, each timestamped and stored so they can be joined by (entity, time):
1. **Signal data** — the forecast/model/feature that drives your probability estimate. For weather, this is NBM probabilistic guidance + station observations; see `references/weather-strategy.md` for the free NOAA/NWS sources (api.weather.gov, NOMADS, AWS Open Data) and how to archive model runs by cycle time.
2. **Market data** — Kalshi prices, bid/ask, order book, volume, open interest over time. Public endpoints need no auth; snapshot on a schedule and/or stream via WebSocket. See `references/kalshi-api.md`.
3. **Ground truth / settlement** — the official outcome each market resolved to, for scoring and backtesting. For weather this is the NWS Daily Climate Report (the *only* settlement source) — note the settlement gotchas in the weather reference (station mapping, local-standard-time high windows).

Design principles: store raw, immutable, append-only captures (never overwrite a snapshot); record the capture timestamp separately from the event timestamp; keep model-run cycle times so you can reconstruct "what did we know at 6am." Fit this into the existing data layer from Phase 0.

**Gate:** You have a dataset you can query as of any past timestamp without leaking future information, joining signal ↔ market ↔ outcome.

---

## Phase 3 — Implement the strategy

Build it modular so each stage can be swapped and tested independently — this pays off across every later iteration.

The pipeline stages:
1. **Signal → probability.** Convert your data into a calibrated probability for each contract/bracket. Calibration matters more than sharpness here: a model that says 70% and is right 70% of the time is tradeable; an overconfident model is not. For bracketed markets (temperature ranges), produce a full probability distribution over brackets that sums to 1.
2. **Edge computation.** Compare your probability to the market price. Edge = your_prob − market_implied_prob, adjusted for the fee you'll pay to enter and exit. Trade only when edge exceeds a threshold that covers costs plus a margin for model error.
3. **Sizing.** Use fractional-Kelly sizing (typically ¼–½ Kelly) so a wrong probability estimate doesn't blow up the bankroll — full Kelly is far too aggressive when your edge estimate is itself uncertain. See `references/backtest-sizing-risk.md` for the formulas and caps.
4. **Order construction.** Build the order against the real order book (Kalshi's book is bid-only by design — YES and NO bids, no asks; you cross by hitting the opposing side's bid). Respect tick sizes and the reciprocal pricing model. Reuse the execution/retry/idempotency plumbing from Phase 0.

Keep the probability model and the trading logic in separate modules — you will iterate on the model far more than on the plumbing.

**Gate:** End-to-end path runs in dry-run mode: given real market + signal data, it emits the orders it *would* place, with logged reasoning (prob, edge, size).

---

## Phase 4 — Backtest where history exists

Backtest to estimate whether the edge is real *after costs*, and to check calibration. Be adversarial toward your own results.

Non-negotiables:
- **Point-in-time inputs only** (Phase 2 enforced this — verify it holds).
- **Realistic fills** — assume you cross the spread (pay the ask side), not that you get mid or best bid. Model partial fills in thin markets.
- **Fees included** — Kalshi's per-contract fee is quadratic in price (near zero at 0/100, maximized near $0.50); apply it on entry and exit. See the fee formula in `references/kalshi-api.md`.
- **Calibration check** — bucket predictions by probability and confirm realized frequencies match (a reliability curve). A profitable-looking backtest with poor calibration is usually overfit or leaking.
- **Out-of-sample discipline** — hold out time periods; don't tune on the data you report on. Watch for regime effects (summer vs winter weather markets behave very differently).

Report edge *net* of costs, hit rate by probability bucket, and drawdown — not gross P&L on a favorable slice.

**Gate:** Positive expectancy after realistic costs, with acceptable calibration, out-of-sample. If not, return to Phase 1 or 3 — do not paper-trade a strategy the backtest rejects. (Some market types have little usable history; if backtesting isn't possible, say so explicitly and lean harder on a longer paper-trading phase.)

---

## Phase 5 — Paper trade against the Kalshi demo environment

Backtests cannot capture live data latency, real fill quality, model drift, or operational bugs. Paper trading against Kalshi's **demo environment** (`external-api.demo.kalshi.co` — full endpoints, fake money, separate API keys; see `references/kalshi-api.md`) is the reality check.

Run the real strategy end-to-end on live markets with simulated capital. Log every decision with full context (as of what data, what prob, what edge, what size, what fill) so paper results are diagnosable. Track the same metrics as the backtest so the two are directly comparable.

Watch specifically for: divergence between paper P&L and backtest expectation, fills materially worse than modeled, signal data arriving later than assumed, and any market-microstructure surprise (thin books, wide spreads, settlement timing).

**Gate:** Live paper behavior matches backtest expectations within reason. If it diverges, **diagnose the cause before proceeding** — divergence is information, usually about fills, latency, or a data assumption that was wrong offline.

---

## Phase 6 — Iterate, then consider live cautiously

Use paper-trading evidence to adjust the strategy — recalibrate the model, retune thresholds, fix fill assumptions — then re-paper-trade. Iterate until stable.

Only then consider live, and treat the transition conservatively:
- **Promotion criteria, decided in advance** — e.g. minimum paper-trading duration covering multiple market regimes, calibration holding live, paper edge surviving realistic costs, no unresolved divergence from backtest.
- **Start tiny.** First live size should be small enough that being wrong about live-vs-paper differences is cheap tuition. Scale only as live results confirm paper results.
- **Keep the kill switch and monitoring** from existing infra wired in from the first live order (use `bot-investigation` / `deploy-check` for health checks and deploy verification).

**Optional uncorrelated ballast:** a delta-neutral funding sleeve on existing Solana infrastructure (e.g. Drift Protocol funding capture) can sit alongside as an uncorrelated income stream reusing what you already run — treat it as a separate, independently-gated strategy, not part of the Kalshi edge.

---

## Guardrails (apply throughout)

- **Demo before production, always.** No new or materially-changed strategy touches production/real money before running in the demo environment.
- **Conservative sizing.** Fractional Kelly with a hard per-position and per-day cap. The bankroll surviving a string of wrong estimates matters more than any single trade's upside.
- **Costs are part of the edge.** An idea that's only profitable gross is not an edge. Always evaluate net of fees and realistic fills.
- **No lookahead, ever.** Point-in-time correctness is the backbone of every valid backtest.
- **Credentials stay manual.** Never automate account creation, password entry, or moving funds; the RSA key lives in the secret store, never the repo, and is unrecoverable if lost (regenerate + re-upload the public key).
- **This is not investment advice** and past performance — backtested or paper — does not guarantee future results. Enter live small and monitored.

---

## Reference files

Read these when you reach the relevant phase (don't front-load them all):
- `references/kalshi-api.md` — Auth (RSA-PSS signing), base URLs (prod + demo), endpoints, pricing/tick model, fee formula, rate-limit tiers, order types, SDKs, WebSocket. **Read for Phases 2, 3, 5.**
- `references/market-edge-map.md` — Kalshi market taxonomy and an honest per-category assessment of where a solo quant has edge. **Read for Phase 1.**
- `references/weather-strategy.md` — The leading worked thesis: temperature markets, NBM probabilistic guidance → brackets, data sources, settlement mechanics and gotchas, modeling approach. **Read for Phase 1–2 if pursuing weather.**
- `references/backtest-sizing-risk.md` — Backtest methodology, fractional-Kelly sizing math and caps, calibration testing, paper→live promotion gates, risk controls. **Read for Phases 4–6.**
