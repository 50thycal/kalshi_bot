# FLOW-VETO — don't post an mmsell bid into active YES buying

*Thesis written 2026-09-29, before the probe ran. The predictions, bars and kill criteria are
pre-registered here and are not re-scoped after results. Operator request (Calvin, 2026-09-29):
"build the checks for 1 and 2 first and see how those play out." Status: **KILL (probe run 2026-09-29) — family closed; see RESULTS.** Companion probe: [`MMSELL_RUNAWAY_CHASE_THESIS.md`](MMSELL_RUNAWAY_CHASE_THESIS.md).*

## One-liner

Before posting an mmsell NO bid, look at the market's public trade tape for the previous 10
minutes. If YES takers outweigh NO takers, someone is actively selling into the NO side, so skip
the post. Those are the orders that fill fast and lose.

## Why — the losers this targets

Fills that land fast lose money; slow fills win.

- **Fmmsell10 live fills** (twin P&L on the same market, ops `mmideas-q2b-0929`):
  - fills within 10 min: 404 trades at −0.73¢;
  - fills after 10 min: 270 trades at +1.46¢.
- **Earlier queue study** (`MMSELL_QUEUE_AWARE_CANCEL.md` §3):
  - fills in under 5 min: −7.8¢ (55 wins, 10 losses);
  - fills after 90 min: +7¢ (29 wins, 0 losses).

**Mechanism.** A fast fill on a post-only NO bid means a YES buyer was already sweeping the NO
side when we joined. The order is not getting the retail flow the strategy sells to; it is the
counterparty to someone buying YES urgently. If that buying is already visible on the tape before
we post, it can be avoided.

## Not a revival — relation to OFLOW (KILLED 2026-07-22)

[`OFLOW_THESIS.md`](OFLOW_THESIS.md) asked whether trailing taker imbalance predicts the **next
price move** well enough to pay a round-trip taker fee. It measured corr ≈ +0.008 on 933k
samples of liquid in-play World Cup markets, and failed. This probe asks a different question:
does pre-post imbalance predict whether **our resting maker order's fill** is adversely selected,
with the loss event being a tail settlement in the 90–97¢ band? The two differ in three ways:

- the loss is a tail settle, not a next-tick drift;
- no fee is paid to act, since a veto is free;
- the universe is the mmsell band, not liquid in-play markets.

OFLOW's null is still the relevant prior. If flow carried little information there, it may carry
little here, and a mechanically new premise does not make it likely. **Prior: LOW-MEDIUM.**

The killed volatility gate A4 (`MMSELL_ROADMAP.md`, −0.21¢ at n=1,944) filtered on price
volatility, not on signed flow at our side, so it is not a duplicate either.

## Pre-registered probe (`scripts/mmsell_flow_veto_probe.py`)

**Dataset (provenance: bot DB for our orders and fills, Kalshi public REST for the tape and
settlement; the two are never mixed into one series).**

- **Orders:** every `live_orders` NO buy of the live tag (default `Fmmsell10`) since its twin
  epoch (default `2026-09-07T02:03:36Z`), with a Kalshi order id.
- **Decision instant:** `execution_order_context.decided_at` when present, else
  `live_orders.created_at`.
- **Fill:** as in the chase probe:
  - `execution_fill_events` exchange ms first;
  - else `fills.raw_fill_json.created_time`;
  - else `fills.filled_at − 300 s`.
- **Tape:** `GET /markets/trades?ticker=T&min_ts=t−W&max_ts=t`, paginated. `taker_side = yes`
  means a YES buyer, the flow that hits NO bids. Volume is `count_fp`, else `count`.
- **Settlement:** `GET /markets/{ticker}` → `result`, used for **scoring only**.

**Veto rule (frozen, parameter-free; primary W = 10 min):** veto when both of these hold in
`[t − W, t)`:

- YES-taker contracts > NO-taker contracts;
- YES-taker contracts ≥ 1.

**Outcome per order (real money):**

- `realized = (settle_NO − L) × filled_qty` if the order filled, else 0;
- a fast fill is one within 10 min of the decision instant.

### Predictions, bars and kill criteria

| id | claim | PASS | KILL |
|---|---|---|---|
| **V0** instrument | the tape is readable for the population | tape fetched for ≥ 90% of orders | < 90% → **HOLD (instrument)** |
| **V1** floor | enough vetoed fills to judge | ≥ 40 vetoed **filled** orders | < 40 → **HOLD** |
| **V2** vetoed fills are losers | the veto removes bad fills | mean realized on vetoed fills ≤ **−1.0¢** | — |
| **V3** separation | kept fills beat vetoed fills | (mean kept-fill − mean vetoed-fill) ≥ **+2.0¢** **and** the bootstrap 5th percentile of that difference (2,000 resamples, seed 20260929) is **> 0** | difference **≤ 0** at V1 floor |
| **V4** capacity | the veto doesn't gut the book | veto rate ≤ **50%** of orders | veto rate > 50% **and** V3 difference ≤ +2.0¢ |

**Decision rule.**

- **PROMOTE** = V0, V1, V2, V3 and V4 all pass. That makes the veto a candidate treatment arm for
  a successor live canary, pending operator approval. Arming is a hard stop, and a probe PASS
  does not authorize it.
- **KILL** = either kill cell trips. Close the family, and record that pre-post flow does not
  predict fill toxicity, so OFLOW's null extends to maker selection.
- **HOLD** = anything else.

**Reported but not decision-driving:**

- W = 5 min and W = 30 min sensitivity;
- fast-fill share in vetoed vs kept orders, which is the mechanism check: the veto should
  concentrate fast fills;
- the veto's net realized effect in $ (−Σ realized of vetoed fills);
- by series.

Only the frozen W = 10 row decides.

**No lookahead.** The tape window ends strictly before the decision instant. Fill timing and
settlement are used only to score.

**Known limits, stated before the run.**

- The public tape is the whole market's; it is not filtered to our price level. The tape for
  pre-epoch days is complete, unlike the WS-019 collector, which only subscribes after we post.
- A vetoed order frees a slot for another candidate, and that value is not counted.
- Per-market SD is about 24¢, so with ~200 vetoed fills the SE of the difference is about 2¢.
  The +2¢ bar is therefore marginal by design; a HOLD is a likely and honest outcome.

## Cost, capacity and value to the $100/month goal

A veto can only remove trades. At best it lifts the live per-fill edge (+0.14¢ today) toward the
twin's. Removing ~200 fills at −1¢ adds about +$2–3/month at 1 contract. It is a
precondition for size (idea #3), because scaling a book whose fills are adversely selected
scales the loss.

## Correlation

This is the same book as Fmmsell10. It is a selection filter, not a new edge.

## RESULTS

**Probe run 2026-09-29 (ops `veto-probe-20260929-1`, code `131ae0f`). Verdict: KILL.**

| gate | result |
|---|---|
| V0 instrument | tape fetched for 999 of 999 orders; 944 settled and scored → PASS |
| V1 floor | 454 vetoed fills → PASS |
| V2 vetoed fills | mean realized **+0.15¢** (bar ≤ −1.0¢) → fail |
| V3 separation | kept +0.32¢ − vetoed +0.15¢ = **+0.17¢**, 5th percentile −3.14¢ → fail |
| V4 capacity | veto rate **59.2%**, above 50%, and separation ≤ +2¢ → **KILL** |

**What it found.** Pre-post flow does predict *speed*: 72.0% of vetoed fills landed within
10 min, against 43.6% of kept fills, so the mechanism check passes. But in this book, speed
does not predict *loss*. The vetoed fills made +0.15¢ and the kept fills +0.32¢, which is
indistinguishable. The veto would have cost real money a net −$0.67 while cutting 59% of the
book. YES-taker flow is simply the normal state of these markets (the longshot buying the
strategy sells into); it is not a toxicity marker. This extends OFLOW's null (2026-07-22) from
next-move prediction to maker fill selection.

**Sensitivity (reported only; does not decide):**

- W=5 min: separation +2.34¢, but the 5th percentile is −0.88¢ and the veto rate is 50.6%;
- W=30 min: separation +0.26¢.

The W=5 row fails the same bars and is not a basis for a re-run. A new window chosen after
seeing it would be a re-scoped hypothesis, and would need a fresh sample.

**Side observation, not a finding.** The vetoed fills by series repeat the in-sample loss cells
already visible in the twin split (KXMLBSPREAD, KXMLSGAME, KXWTA*). The series explains the
losses; flow does not. That is a universe question, which belongs to idea #3 or the existing
universe work, not to this probe.

**Decision (pre-committed).** Family closed: pre-post tape flow does not identify toxic mmsell
fills.
