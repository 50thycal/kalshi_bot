# OFLOW — is there within-market order-flow alpha on Kalshi's own trade tape?

*Thesis written 2026-07-22 (idea-model Area 2: microstructure / order-flow), before any
validation ran; the predictions below are pre-registered. Status: **RULING-OUT / KILL** (probe run
2026-07-22; see RESULTS). **Feasibility-first**, and the **LOW prior held.***

## RESULTS (2026-07-22 probe run — `oflow_study`, ran first-try on 823k Kalshi trades)

**Verdict: KILL — order-flow-as-signal is closed on Kalshi's tape, and per-market microstructure
collection is NOT worth building.** The probe read **823,092 Kalshi trades (100% with a usable
`taker_side`)** across 27 markets → **933,073 no-lookahead (imbalance, forward-move) samples.** The
result is unambiguous:

- **Imbalance→next-move correlation ≈ +0.008** (pooled); per-market correlations are all tiny and
  sign-inconsistent (−0.03 to +0.10).
- Conditional on strong trailing imbalance (|z|≥1), the **mean forward directional move is +0.05¢
  gross** — i.e. zero — so **net of the ~3.4¢ round-trip taker fee it is −3.40¢.**
- The **imbalance quintiles are flat** (Q1 +0.02¢ → Q5 −0.01¢): a strong buy-imbalance does not
  predict an up-move. The large-trade (toxicity) slice is identical (−3.40¢) — no informed-size
  gradient (**P2** fails too).
- Per the pre-registered **P1 kill criterion** (net ≤ 0 or hit ≤ 50%): **FAIL.** Flow imbalance
  does not predict the next move net of cost — the quote already reflects the flow / it's retail
  noise / cost eats it.

**Decision (pre-committed):** the microstructure/order-flow family is ruled out on Kalshi's tape,
and — the money-saving half — the per-candidate tape/book collection (`MMSELL_FILL_MODEL.md`
follow-up #2) is **not worth building** for a signal that isn't there. Exactly the LOW-prior
expectation, obtained cheaply on data already collected. Honest caveat: this is the liquid in-play
WC slice, so the precise finding is "no *within-market* flow→price predictability net of cost
here," consistent with `edge_research` lesson 5 (cost dominates on liquid Kalshi markets).

## One-liner

On the only real order-flow data the bot has collected — the ~1.08M-trade World Cup in-play tape
(`game_tape_snapshots`, `taker_side` populated) — test whether **trailing net aggressor imbalance
predicts the next ~60-second price move by more than the taker round-trip cost.** It is the "is
there *any* microstructure alpha on Kalshi's tape?" gate that decides whether the whole
order-flow-signal family is worth pursuing (and worth building per-market tape collection for).

## Mechanism

- **What might be mispriced:** informed aggressor flow (net taker-buying of a team) pushes price;
  if the quote lags the flow, a fast taker could ride the completion of the move.
- **Who's on the other side / why it might persist:** Kalshi is retail-dominated with
  cycle-cadence (60s+) re-quoting, so a quote *could* lag a burst of aggressive flow.
- **Honest prior: LOW.** Three graveyard facts weigh against it: (1) `edge_research` lesson 5 —
  cost dominates on liquid markets (cross-venue divergence was ~1–1.6¢ vs a 2–4¢ round-trip); (2)
  this is the **killed** World Cup slice, and the xgame book already found the cross-venue price
  feed is *symmetric* (both venues track a shared third feed); (3) the markets are h2h winners —
  the exact −EV cell mmsell's live test lost in. **BUT the within-market order-flow → price
  question has NO prior attempt** — the xgame probe measured cross-venue *price* lead-lag, never
  within-market *flow*. So this is a genuinely new family, and a clean ruling-out is new
  information.
- **Edge family:** microstructure / order-flow — first attempt. (Not obs-pin, not maker; a
  distinct return driver.)

## Data reality this sits inside (why feasibility-first)

An ops query confirmed the Area-2 blocker: full-depth `orderbook_snapshots` exists only for 77
scanner markets over 3 stale June days; the **markets we trade have no microstructure data
collected** (the mmsell tracker never persists books — `MMSELL_FILL_MODEL.md` §2). The one real
order-flow dataset is this closed WC tape. So OFLOW is a **feasibility read on the tape we have**,
not a live book: it decides whether the order-flow family is alive enough to justify building the
per-candidate tape/book collection (`MMSELL_FILL_MODEL.md` follow-up #2) that every *live*
microstructure probe would need.

## Pre-registered predictions (the gate)

- **P0 — the data supports the test.** `taker_side` is populated on the Kalshi trades and there is
  enough within-market density to build ~10s bars with a trailing flow window. FAIL → HOLD (the
  tape can't answer it; the aggressor side wasn't stored) — not a kill.
- **P1 — flow imbalance predicts the next move, net of cost.** Conditional on a strong trailing
  imbalance (top/bottom tercile per market), the mean forward move *in the imbalance direction*,
  net of a worst-case round-trip taker fee at the entry band, is **≥ +2¢** with hit-rate **> 50%**
  (and a positive imbalance→move correlation). **KILL if the net directional move ≤ 0 or hit-rate
  ≤ 50%** — flow is noise / the quote already reflects it / cost eats it. This is the expected
  result given the LOW prior, and it cleanly rules out the family.
- **P2 — toxicity gradient.** Imbalance computed from *large* trades only predicts the forward move
  **more** than all-trade imbalance (informed size vs retail noise). Corroborating, not decisive.
- **P3 — not stale momentum.** The signal is concentrated in the strong-imbalance tail (event-
  conditional), not a weak all-bars average, and the raw imbalance→move correlation is not merely
  price autocorrelation the quote already carries.
- **Decision rule (FEASIBILITY, pre-committed):** if **P1 fails**, rule out order-flow-as-signal on
  Kalshi, log it, **and do NOT build the per-market tape collection** (the probe just saved that
  eng cost). If **P1 passes**, the family is alive: the next step is to **build per-candidate
  tape/book collection for tradeable markets**, then re-probe on *live, tradeable* markets (not
  this killed WC slice) before anything becomes a book. No book is built off this slice regardless.

## Probe plan

- **Script:** `scripts/oflow_study.py` (allowlisted in `ops_runner.py`). Read-only DB via
  `psycopg` + `DATABASE_URL_RO`, exactly like `scripts/db_query.py`; reuses the ~10s bar-building,
  `taker_fee_c`, and RO-connection pattern from `scripts/xgame_tape_study.py`.
- **Dataset + provenance:** `game_tape_snapshots` (Kalshi venue rows only for v1 — clean
  `taker_side` = yes/no → signed flow in the team-probability direction). Provenance is the
  cross-venue tape collector, kept separate from the live weather/crypto tables.
- **No-lookahead:** the trailing imbalance at bar *t* uses trades **strictly before *t***; the
  forward move is measured from the bar-*t* quote to *t+H*, strictly after. The outcome never
  informs a point before it occurred.
- **Measurement:** pooled (per-market-normalized) imbalance→forward-move correlation; mean forward
  directional move (gross + net of `2·ceil(7·p·(1−p))`¢) and hit-rate conditional on strong
  imbalance; a large-trade (toxicity) slice; per-market sanity. Reports the raw correlation **sign**
  so a `taker_side` mapping flip is visible rather than silently absorbed.
- **v2 (only if P1 passes):** the cross-venue flow slice (Polymarket aggressor flow → Kalshi move)
  is deferred — the PM `taker_side` (buy/sell) → team-direction mapping is ambiguous without the
  traded token side, and shipping a guess would be worse than omitting it.

## Cost + capacity

- **Cost:** the ~2–4¢ worst-case taker round-trip is the bar the signal must clear — the whole
  point of the test. Fee = `ceil(7·p·(1−p))`¢ per leg at the entry band.
- **Capacity:** moot for v1 — the slice is closed (WC ended), so this is a feasibility read, not a
  sizeable book. If it passed, capacity would be re-assessed on live tradeable markets after
  collection.

## Correlation

- **Vs current book:** a short-horizon order-flow signal is a *different return driver* from the
  forecast/obs books (weather, theta, mmsell) — **uncorrelated ballast if it were real**. But the
  LOW prior means the likely value is a cheap **ruling-out** (and saving the collection build),
  not a new edge.
- **Value to $100/mo:** either it opens a genuinely new uncorrelated family (unlikely) or it closes
  it cheaply and redirects effort — both are positive for the research program.
