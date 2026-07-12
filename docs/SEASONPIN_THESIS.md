# SEASONPIN — post-decided discount on season win-total ladder rungs

*Thesis written 2026-07-12, before any validation ran; the falsifiable predictions below are
pre-registered. Promoted from `docs/IDEA_MODEL_20260712_run2.md` (S1), the
settlement-mechanics-hunt scoped run.*

**Status (2026-07-12, recon census run):** MLB (the named primary target) is **HOLD —
UNTESTABLE-yet**, 0 settled rungs (too early in the season; expect Aug onward). WNBA (a
same-family extension the census's dynamic discovery surfaced, not originally scoped here) is
**BORDERLINE** — 40 settled/decided rungs, exactly at the P1 n-floor, with candle-coverage and
real volume both still unconfirmed. Full probe (`kalshi_seasonpin_study.py`) intentionally not
yet written for either family — see "SEASONPIN CENSUS 2026-07-12" in `RESEARCH_JOURNAL.md` for
the full readout and the recommended follow-up before a probe/no-probe call.

## One-liner

On Kalshi season win-total ladders (MLB now, NFL from September), each rung's outcome becomes
**arithmetically decided mid-season** — YES clinched at the Nth win, NO clinched when
`wins + games_remaining < N` — while quotes on these background markets lag the standings
arithmetic; buy the decided side at a discount and hold to settlement.

## Mechanism

- **What mispricing:** rungs whose outcome is already certain (elimination or clinch, by
  arithmetic on public game results) trade off 0/100 — the decided side is available below fair
  value. Decidedness accrues **game-by-game with no single decisive event**: a rung usually goes
  impossible mid-way through some unrelated Tuesday blowout, not at a headline moment.
- **Why it exists / who's on the other side:** season ladders are slow background markets —
  retail holds "hope" positions and stale resting orders that nobody re-marks daily; attention
  (retail and MM) sits on game markets, not on 30 teams × ~15 rungs of season arithmetic. The
  per-rung magic-number math is trivial but tedious — exactly a mechanics-blindness surface.
- **Why it persists:** ~450 rungs drifting for months; each individual decision event is small,
  unscheduled, and unannounced. No feed screams "the ≥85 rung of a 60-win-pace team just went
  dead." The counterparty isn't racing anyone; they're not looking.
- **Edge family:** obs-pin / mechanics-blindness — the **only family that has ever passed**
  (PIN15; scorecard 2026-07-12). Same shape as pin15/COMPIN: outcome determined by settlement
  arithmetic before the official settle, quote lags during the gap — here with a **days-to-weeks
  latency budget** (the slowest-clock member of the family yet).
- **Named dead parents, and the material difference:**
  - **CLINCHMATH / WCPROP** (killed — derived ladders reprice within one cycle): those were
    *event-triggered recomputes* on winner ladders after a decisive, watched result. SEASONPIN is
    *slow-accretion decidedness* on cumulative-count rungs — no decisive moment, no repricing
    trigger for the crowd to react to. The COMPIN-vs-EIALAG distinction, applied to sports.
  - **TOUCH-LOCK / PINNED** (killed — post-public convergence is fast): the YES-clinch side (Nth
    win = a touch) is plausibly that dead shape, and possibly early-expired by the exchange.
    The thesis therefore leans on the **elimination (NO) side**, which is not a touch — it
    accrues via losses and calendar — and P3 tests the two sides separately.
  - **tfav / PINNED's favorite-drift control** (generic favorite pricing): buying a decided side
    at 90–96¢ looks like favorite-buying. P2 forces the post-decided EV to beat a pre-decided
    favorite-buy control on the same ladders, or the thesis dies as repackaged tfav.

## Known unknowns the census must settle (pre-registered, not assumed away)

1. **Early expiration.** If Kalshi early-settles a rung once determined (standard on some sports
   contracts), the post-decided window shrinks from weeks to the exchange's determination
   latency, and the edge may not be capturable by a slow loop. The census measures the realized
   gap (decided-date → settled-date) on the 2025/2026 tape; **if the median gap is <24h, KILL**
   without writing the full probe.
2. **Capital lockup.** Without early expiration, a rung decided Aug 1 settles late September —
   cents earned must justify weeks of locked capital. The decision rule requires ≥3¢ net, which
   at 90–96¢ entry over ≤10 weeks is acceptable turn; anything under 2¢ is not worth the lock.
3. **Spread reality.** Background ladders may quote wide. All EV is measured against the real ask
   (taker entry), not mid — the pin15 discipline.

## Pre-registered predictions (each with a kill criterion)

- **P1 — post-decided discount exists and clears cost.** Over all rungs decided ≥24h before
  settlement in the census window (2025 season + 2026 season-to-date), taker-buying the decided
  side at the first post-decided ask nets **≥ +3¢/contract** after the entry-leg fee
  (`ceil(0.07·P·(1−P)·100)`¢), with **n ≥ 40** decided-rung observations that have candle
  coverage. PASS ≥ +3¢; **KILL < +1.5¢**; between → grey, hold for the other predictions.
  (If n < 40 with candle coverage, the verdict is UNTESTABLE-yet → HOLD, not a probe re-scope.)
- **P2 — it is not favorite drift.** The same-window control — taker-buying *undecided* rungs
  priced 85–95¢ on the same ladders — must underperform the post-decided book by **≥ 2¢/contract
  net**. KILL if the control matches or beats post-decided EV (PINNED's exact failure).
- **P3 — the edge lives on the elimination side.** Sliced YES-clinch vs NO-elimination, the
  elimination side alone must satisfy P1's bar. If only the clinch side clears, treat as
  TOUCH-LOCK-reborn and KILL (fast post-touch convergence is a race we lose).
- **P4 — the window is slow enough for our loop.** The discount at decided+24h retains ≥ 60% of
  the discount at decided+1h. KILL if the discount is gone within the first hours (we run a
  minutes-scale loop, not a colo race).
- **Decision rule:** build the paper book (`seasonpin`, elimination-side entries only unless P3
  passes both sides) **only if P1, P2, P3, and P4 all pass**. Any KILL criterion → close the
  thesis and log it; do not re-scope thresholds after seeing data. A paper book gates at
  **n ≥ 100 settled, keep only if per-trade > +1.5¢ net**, mandatory `BOOK_REGISTRY.md` row at
  first trade.

## Probe plan (staged — recon census FIRST)

- **Recon census (step 1, cheap, ~20 lines of reads):** answers ONLY "does a gradeable tape
  exist?" — (a) discover the win-total series tickers via the public events/series endpoints
  (win-totals category; expect `KXMLBWINS`-style series; do not hardcode), (b) count settled vs
  open rungs, per-rung volume and current spread, (c) pull contract rules text for the
  **early-expiration clause**, (d) for ~20 sampled settled rungs, compare decided-date
  (reconstructed from public game logs) vs Kalshi settled-date to measure the real window.
  Below the n-floor (40 candle-covered decided rungs) or median window <24h → HOLD/KILL
  respectively, **no full probe written** (the FREEZE/COMPIN lesson).
- **Full probe (step 2, only if census clears):** `scripts/kalshi_seasonpin_study.py` — read-only,
  stdlib-only, public Kalshi REST (`/events`, `/markets`, candlesticks `period_interval=1440`
  then `60` around decided-dates) + MLB results from the free MLB Stats API (regular-season game
  logs; NFL later via nflverse dumps). Needs allowlisting in `ops_runner.py`: yes. Reuses the
  candle/fee tooling patterns from `kalshi_decay_study.py` / `kalshi_pinned_study.py`.
- **Dataset + provenance:** Kalshi public REST history + league game logs, fetched inside the
  probe; no DB tables touched, never mixed with the live `weather_*`/crypto collectors.
- **No-lookahead construction:** decided-time for a rung = the timestamp the arithmetic first
  crossed (wins ≥ N, or wins + remaining < N) computed **only from games already final at that
  timestamp**; entry price = first ask **at or after** decided-time + loop latency (≥5 min);
  never use same-day closing candles to justify an intraday entry.
- **Measurement:** per-rung net ¢/contract at decided+1h / +24h / +72h entries; win% (should be
  ~100% — any decided-side loss is a red-flag bug, the FREEZE-v1 discipline); P2 control EV;
  clinch-vs-elimination split; split-half by team to catch cell concentration.
- **Promotion result:** P1–P4 pass → paper book `seasonpin` riding the live cycle
  (elimination-side, taker, hold-to-settle), NFL ladders added in September under the same gate.

## Cost + capacity

- **Fee/spread math:** entries at 85–96¢ cost ~0.3–0.9¢/contract in fees (one leg; settlement is
  free). Real cost is the spread — background ladders can quote 2–6¢ wide; EV measured vs ask.
- **Adverse selection:** none structural — taker entries on an outcome already certain; the only
  seller who "knows something" is one who knows the arithmetic too (then no discount exists and
  we simply don't trade).
- **Capacity:** ~450 MLB rungs/season, most deciding between mid-July and late September (i.e.,
  starting NOW), then ~500+ NFL rungs from September — a rolling stream of small certain-payout
  entries rather than one lumpy event. Volume per rung is modest (not top-50 series); the census
  measures whether $5–20/rung is realistic. Honest floor: this may be a $20–60/month book, not a
  $100/month book — acceptable as uncorrelated ballast if per-trade EV is real.

## Correlation

- **Vs current book:** same *category* as mmsell (sports) but a different return driver —
  mmsell is maker-sold longshot decay (FLB harvesting, filled on flow); SEASONPIN is taker-bought
  arithmetic certainty (no forecast, no fill dependence). Partial flow overlap: a decided-dead
  rung at 5–10¢ is exactly what mmsell3 might be short already — the census flags rungs where
  both books would stack the same side so the paper books never double-count the same cent.
  Zero correlation with pin15/theta4 (crypto) and weather.
- **Value to $100/mo:** a third live expression of the one validated family, on an underlying
  (season arithmetic) uncorrelated with crypto vol and weather; adds settle-cadence in
  Aug–Jan (MLB→NFL) when weather books are the portfolio's only daily grind.
