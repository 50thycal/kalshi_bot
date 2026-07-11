# PIN15 — endgame settlement-average observation-pin on Kalshi's 15-minute crypto up/down

*Thesis written 2026-07-11, before any validation ran; the falsifiable predictions below are
pre-registered so the test can't be quietly re-scoped after the fact. Promoted from the 2026-07-11
idea-model run (`docs/IDEA_MODEL_15MIN_CRYPTO_20260711.md`). Status: **pending probe.***

## One-liner

In the final ~90 seconds of a Kalshi 15-minute crypto window, the settlement is the **60-second
average** of the CF Benchmarks index — a *partially-observed, deterministic* number — so when the
already-locked-in partial average has pushed the outcome to one side, take the value side against
the retail quote that is still anchored to the flashing last-tick price. Both directions qualify;
held to settlement (expires in seconds — no exit).

## Mechanism

- **What mispricing:** the market quote near the close tracks whether the **last-tick spot** is
  above/below the target; the contract actually settles on the **average of the final 60 one-second
  index prints**. These diverge exactly when spot is moving late in the window. Retail overpays for
  the side the last tick favors and underpays the side the running average favors. The sharpest
  cell (**SPIKEFADE**): a spike in the final 5–10 s drags the last tick across the target and
  retail chases "Up" toward 80–90¢, but ~50 of the 60 averaged prints already landed on the other
  side, so the spike barely moves the settle and the *opposite* side is the value.
- **Why it exists / who's on the other side:** retail lottery flippers watching a live price
  ticker, whose mental model is "is the number above the line *right now*" — not "what is the
  60-second average going to be." Kalshi's own docs and third-party guides flag this exact trap
  ("the last tick is not the settlement price"), which is evidence it's a *widespread* misread, not
  a niche one.
- **Why it persists:** it's a behavioral + mechanical asymmetry, not a locked arb and not a venue
  race. There is **no symmetric fast venue** arbing it (Polymarket has no 15-min crypto), so it is
  NOT the xgame trap where both venues track a shared feed. Pro MMs may fade it, but the retail
  flow is large, the venue is a declared "lottery," and each window is a fresh, tiny, independent
  bet — the same warehouse-many-small-uncorrelated-expiries shape that lets a small automated book
  harvest what a balance-sheet MM prices up.
- **Edge family:** **observation-pinning / staleness** — the fresh signal (the running
  partial-average of the settlement window) is *deterministic about the outcome*, not a forecast.
  This is the surviving branch of the staleness family (obs/con), the current highest prior
  (meta-lesson 2), and the explicit material difference from theta, which was a *model-vs-quote*
  play on the hourly ladders — the branch that keeps dying. **PIN15 does not need a good vol model
  to be right; it needs the clock.** (A short-horizon residual-vol estimate only sets *how early*
  it's safe to act — it is a latency knob, not the edge.)

## Pre-registered predictions (written BEFORE validating; each with a kill criterion)

Notation: at decision time T (seconds before close), `A_T` = the *realized* partial average of the
1-second index prints already observed in the settlement window; `p_pin` = model P(Up settles),
computed from `A_T` plus a short-horizon distribution of the residual seconds (theta's `SpotModel`,
horizon = seconds to close). "Signal side" = argmax(p_pin, 1−p_pin). All EV in ¢/contract **net of
both-leg worst-case fees** at the entry price.

- **P1 — The signal exists and de-randomizes early enough to matter.** On settled KXBTC15M windows,
  at **T = 60 s** the model `p_pin` is confidently off the coin flip (|p_pin − 0.5| ≥ 0.15) in a
  materially non-trivial fraction of windows, and its **realized directional hit-rate matches
  p_pin within ±5pp** (i.e. the pin is *calibrated*, not just confident). PASS if calibrated and
  the confident-fraction is ≥ ~15% of windows at T=60 s; **KILL** if the outcome is essentially
  undetermined until the final <10 s in ≥90% of windows (then it's un-actionable at any realistic
  latency → dead-for-us).
- **P2 — The quote lags the pin (the tradeable edge, net of fees).** Taking the signal side at the
  market price at T is **positive net of both-leg fees**, overall and split-half OOS. PASS if
  **net EV ≥ +1.5¢/contract** at some entry latency T ≥ 30 s (so a tightened poll can hit it);
  **KILL** if net EV ≤ 0 at every T ≥ 15 s (the quote already prices the average — venue efficient
  for us).
- **P3 — The edge is latency-shaped and concentrated (the decomposition).** Net EV **rises as T→0**
  and is **concentrated in the SPIKEFADE / high-residual-move cell** (windows with a late spot move
  ≥ ~0.1% in the final minute) and in **higher-realized-vol windows** (VOLREGIME) — i.e. it lives
  where the last-tick-vs-average gap is largest, not uniformly. PASS if the top cell is ≥ 2× the
  pooled EV; a flat/uniform profile is a red flag the "edge" is a lookahead artifact → re-audit.
- **P4 — Robustness.** P1–P2 hold on both halves of the date range, do not invert on high-vol
  days, and hold on ETH (the #2-depth twin) — not only BTC. Directional symmetry: the Up-fade and
  Down-fade cells are both non-negative (a one-sided result signals a bias/lookahead bug, cf.
  MLBWX v1).
- **Structural co-checks (measured in the same probe, not gates):** **UPBIAS** — is `Up_ask +
  Down_ask` overround persistently asymmetric (retail Up-bias)?  **OPEN15** — does the open-anchor
  drift version (act at T≈13 min on early spot drift, more latency budget) show *any* residual EV,
  or is the long-horizon version fully efficient? These sharpen the follow-on, they don't decide
  PIN15.

**Decision rule (pre-committed):** build the PIN15 paper book **only if P1 (calibrated pin) AND P2
(net-of-fee EV ≥ +1.5¢ at T ≥ 30 s) both pass.** If P1 passes but P2 fails at every T ≥ 15 s →
the venue is efficient for our latency; log it, shelve, keep the finding. If P2 passes only at
T < 15 s → **dead-for-us** (can't act that fast); log the "edge exists but is un-actionable"
verdict — a clean cheap ruling-out, per the north star. No parameter tweaks mid-window; the probe
is the experiment. Magnitudes get re-checked against the paper book before any live dollar.

## Probe plan

- **Script:** new read-only `scripts/kalshi_pin15_study.py`, allowlist in `ops_runner.py`
  (`ALLOWED_SCRIPTS`). Reuses theta's `SpotModel` construction pattern and the `xvenue_leadlag`
  `_get/_num` REST helpers. Stdlib + the existing Coinbase/Kalshi public REST clients; no DB write,
  no collector, no live money.
- **Datasets + provenance (kept strictly separate, never mixed silently):**
  1. **Settled KXBTC15M (+ KXETH15M) outcomes + targets** — Kalshi public REST (settled markets:
     target/open reference, settle result, close_time). Provenance: Kalshi REST history, tagged
     separately from the live `crypto_*` collector tables.
  2. **1-second (or finest available) spot** over each window's final minute — Coinbase Exchange
     public API, the same feed theta's `crypto_spot_candles` uses. This reconstructs `A_T`, the
     running 60-second settlement average, point-in-time at each T. (BRRNY vs Coinbase basis is
     cents; P1's calibration check measures the pin *as reconstructed*, so any basis cost shows up
     in the verdict, not in a production surprise — the same discipline theta's P3 used.)
  3. **Quote path in the final minute** for P2 — the honest gap: Kalshi candlesticks are 1-minute
     OHLC, too coarse for T=30/60 s quote reconstruction. **Phase A** of the probe measures P1 and
     P3 from spot + outcomes alone (no quote needed — establishes the signal exists and its
     latency/cell shape) and estimates P2 against the *1-minute-candle* quote as a lower-resolution
     first pass. **Phase B** (only if Phase A's P1 passes) specifies a short **live orderbook
     micro-collection**: a throttled final-90-seconds top-of-book poll on the ~4 near-close BTC/ETH
     windows per hour into a new provenance-separated `crypto_15m_endgame_quotes` table (fail-soft,
     same discipline as the weather snapshots), giving the sub-minute quote path P2 truly needs.
     Phase B is a small data-collection task the thesis pre-authorizes *only* if Phase A clears P1.
- **No-lookahead construction:** at each decision T, `A_T` uses only the ≤(60−T) prints already
  observed; `p_pin` uses only spot ≤ T and a residual-vol estimate fit on a *prior* window; the
  entry price is the quote at T (never the settle). The realized outcome is used only to grade,
  never in the decision. Split-half by date for OOS.
- **Measurement:** per T ∈ {90, 60, 45, 30, 15, 5} s and per cell (spike/no-spike, vol quartile,
  asset, Up-fade/Down-fade): confident-fraction, pin calibration (realized hit% vs p_pin),
  net-of-fee EV, win% vs implied, split-half. Plus the UPBIAS overround series and the OPEN15
  long-horizon EV as co-checks.
- **Promotion result:** P1 + P2 pass → build the `pin15` paper book (a tracker in
  `kalshi_bot/pin15/` riding a **tightened final-minute poll** for a handful of near-close windows,
  entries per the signal-side rule, held to settlement via the shared paper engine) and forward-test
  in the standard `paper_trades` rollups next to every other book.

## Cost + capacity

- **Fee math (the hard gate):** `ceil(0.07·qty·P·(1−P)·100)` ≈ **1.75¢/leg at 50¢**, but the pin
  side is *taken after it has left the middle* — at an entry of 30¢/70¢ the fee is ~1.5¢/leg, at
  20¢/80¢ ~1.1¢/leg. Round trip is one leg (entry) + settlement (no settle fee) since we hold to
  expiry. P2's +1.5¢ bar is set *after* this fee. The SPIKEFADE cell, entered against an
  80–90¢ mispriced quote (fee ~0.6–1.1¢), is where the math is most comfortable.
- **Adverse selection:** PIN15 is a **taker** (cross to the value side), so no maker
  adverse-selection haircut — we pay the spread, already counted. The one selection risk is that
  the quote we cross has *already* absorbed the average (P2's null); the latency-curve measurement
  is exactly the test of that.
- **Capacity:** BTC 15-min = the deepest short-dated crypto contract (hundreds–thousands within 2¢
  of mid); ETH twin next. ~96 windows/day/asset, but we only need the near-close minute of a chosen
  handful → still **hundreds of independent settles/week**, a readable track record in days, not
  quarters. Small size (paper 1–5), one entry/window, per-hour concentration caps.

## Correlation

- **Vs current book:** return driver = "public deterministic signal (the running 60s settlement
  average) that the quote lags." Uncorrelated with **theta** (hourly-ladder homegrown-vol-model
  tail-sell — a *different* mechanic, family, cadence, and price band), with **mmsell** (sports
  favorite-longshot), and with **weather con** (temperature). Its nearest cousin is the obs/con
  *shape* (observation-pinning), which is a point in its favor (surviving family) but a *different
  underlying* (crypto seconds vs weather stations) → near-zero realized correlation.
- **Value to $100/mo:** genuine uncorrelated ballast on the highest-liquidity recurring venue on
  the exchange, in the surviving edge family, with capacity the weather books never had. If P2
  clears at a tradeable latency, this is the first crypto book whose edge does **not** depend on a
  homegrown vol model being right — the exact failure that killed theta.

## Honest limitations

- The whole thesis rests on the 60-second-average settlement being real and on retail anchoring to
  the last tick; P1's calibration check is the direct test of the first, and P2 vs the quote path
  is the test of the second.
- Phase A can prove the *signal* from spot alone but can only bound P2 with 1-minute candles; a
  clean P2 verdict needs the Phase-B sub-minute quote micro-collection — pre-authorized only if
  Phase A clears P1, so we never build collection infra for a signal that isn't there.
- Latency is the live-execution risk the paper book must confront: even a proven edge at T=30 s
  requires a poll cadence the current 300s worker loop doesn't have — the paper book is specified to
  run a tightened final-minute poll on a *small chosen set* of windows, and its realized fills are
  the only true test of whether we can actually be there. That is deliberately the last gate, not an
  assumption baked into the backtest.
