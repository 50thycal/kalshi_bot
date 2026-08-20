# Research Lab — live fill selection and adverse selection

**Session:** Research Lab, 2026-08-20. **Status:** analysis. **Research only —
no strategy parameter changed, no v2 created or frozen, no live restart.** All
three live books remain stood down.

The question: **are the opportunities that actually fill live systematically
worse than comparable opportunities that were quoted but did not fill?**

The instrument that makes this answerable is the **paper twin**. It books a trade
on every entry the strategy attempts, filled or not — so on a market live quoted
and missed, the twin records what that trade *would* have earned. Both sides of
the comparison are therefore measured by the **same instrument** under the same
mechanics, which is what makes filled-versus-unfilled a fair contrast rather than
a comparison of live economics against paper economics.

---

## 1. Evidence for or against fill selection, by book

Twin counterfactual economics, split by whether live actually filled:

| book | filled | quoted-but-unfilled | **haircut** | 95% CI | one-sided p |
|---|---|---|---|---|---|
| **theta4** | **−6.58¢/ct** (n=66) | **+19.59¢/ct** (n=12) | **+26.17¢/ct** | [+5.9, +46.5] | **0.0057** |
| `Lmmsell8` | −27.97¢/ct (n=16) | −13.92¢/ct (n=4) | +14.04¢/ct | [−29.8, +57.9] | 0.265 |
| `Lmmsell10` | +6.08¢/ct (n=61) | +8.36¢/ct (n=24) | +2.28¢/ct | [−9.1, +13.6] | 0.347 |

n counts **settled markets** — the independent unit, since contracts on one
market share one settlement. σ per market is taken from each book's own measured
dispersion (theta4 33¢, mmsell 24¢, `Lmmsell8` 40¢).

> **theta4: strong evidence FOR fill-selection adverse selection.** The markets
> that filled were worth **26¢/contract less** than the ones that didn't, on the
> twin's own books. Supporting: **12 of 12** unfilled markets settled as wins
> against **52 of 66** filled (78.8%); P(12/12 | 0.788) = 0.057.

> **`Lmmsell10`: no detectable selection haircut.** +2.3¢ with a CI straddling
> zero. Both sides positive.

> **`Lmmsell8`: not identified.** The point estimate has the same sign as
> theta4's and a similar magnitude, but with **four** unfilled settled markets the
> interval spans −30 to +58. This book cannot answer the question at its current
> sample, and saying so is the finding.

### Execution fidelity is not the problem anywhere

The paired live-vs-twin gap, conditional on live having filled — the same market,
both legs settled:

| book | paired gap | reading |
|---|---|---|
| theta4 | **−0.23¢/ct** (38 paired markets) | live and twin realize essentially the same thing |

Set against theta4's **unpaired** gap of **+9.30¢/ct**, this is the whole story
in two numbers: on the markets live holds it performs exactly as paper predicts,
and it is nonetheless ~9¢/contract worse overall — because of *which markets it
ends up holding*.

---

## 2. Estimated selection haircut by book

| book | haircut | confidence |
|---|---|---|
| **theta4** | **+26¢/contract** | significant (p=0.006), CI [+5.9, +46.5] |
| `Lmmsell8` | +14¢/contract | not identified — n=4 controls |
| `Lmmsell10` | +2¢/contract | not distinguishable from zero |

The haircut is the amount by which the *counterfactual* value of a filled
opportunity falls short of an unfilled one. It is a measure of **which
opportunities the market chooses to give us**, not of how we execute them.

---

## 3. The 2026-08-19 stress case — regime versus selection

**The regime.** BTC ranged **64,120 → 69,923 (+9.05%)** and ETH **1,905 → 2,314
(+21.50%)** intraday. For books that sell the YES tail — betting a strike is
*not* reached — a violent up-move is precisely the adverse regime.

**What each book did, on markets entered that day:**

| book | markets | contracts | live P&L | fees | twin on the SAME markets |
|---|---|---|---|---|---|
| theta4 | 29 | 88 | **−$18.15** | $0.00 | **−$18.34** |
| `Lmmsell8` | 5 | 10 | −$1.20 | $0.00 | −$1.35 |
| `Lmmsell10` | 25 | 50 | **+$1.39** | $0.01 | +$0.55 |

**The decomposition, for theta4:**

```
broad crypto regime loss     −$18.34   (the twin lost the same on the same markets)
+ fill-selection haircut       ~$0      (within the filled set — see below)
+ execution / slippage        +$0.19    (live BEAT its counterfactual, marginally)
+ fees                         $0.00
= live realized degradation  −$18.15
```

**On the filled set, Aug 19 was regime, essentially in full.** Execution
contributed **+19¢** — in our favour — and fees nothing.

**The selection cost that day appears as foregone profit, not as loss.** The five
markets theta4 quoted and did not fill would have returned **+$3.13** (+19.6¢/ct).
So the day's *opportunity* cost of selection is about **$3**, against an $18
regime loss.

**Two honest limits on this decomposition:**

* **It is not identified between "selection within the filled set" and "regime".**
  The twin lost the same amount on the same markets, which bounds *execution* to
  near zero but cannot separate "these markets were doomed by the rally" from
  "these were the markets the rally was about to run over, which is why they
  filled". Both produce identical numbers here. The epoch-level haircut (§1) is
  what argues for the second reading; a single day cannot.
* **The day's total does not reconcile exactly with the −$24.95 figure.** This
  query keys on **order entry date** and counts only positions that have since
  settled; a settlement-date accounting, or one including entries from prior days
  that resolved on the 19th, will differ. The gap is definitional, not a
  disagreement about the facts, and I did not force the two to match.

**`Lmmsell10` was profitable on the day of a 9%/21% crypto rally** — which is the
whole of §5.

---

## 4. Do live fills predict adverse subsequent movement?

**Not answerable with the data as it stands.** Reported as measured, with the
coverage that undermines it:

| | markets | with a quote at t₀ | pre-5m | post-1m | post-5m | post-15m |
|---|---|---|---|---|---|---|
| FILLED | 115 | **34** | −7.60¢ | +5.95¢ | +5.77¢ | +6.24¢ |
| QUOTED-UNFILLED | 22 | **3** | 0.00¢ | −34.67¢ | −34.67¢ | −38.00¢ |

Movement is in YES-mid cents; theta4 holds NO, so **a rise is adverse**.

The filled pattern is textbook toxic flow — the YES price **falls 7.6¢ into our
resting bid**, we fill, and it then **reverses up ~6¢ against us** and stays
there through 15 minutes. If it held up it would be direct evidence that a fill
predicts adverse movement.

**It does not hold up, because the control group is three markets.** Only
**3 of 22** unfilled markets have a ladder quote at their anchor time, against
34 of 115 filled. Worse, the coverage asymmetry is itself plausibly
selection-driven — a market with quotes recorded near our anchor is a market that
was actively quoting — so the missing 19 are not missing at random.

**Verdict: suggestive, not established.** The direction is consistent with §1 and
§3, and the test should be re-run once the telemetry in §7 exists. I am not
counting it as evidence.

---

## 5. Why `Lmmsell10` behaved differently

**Market mix, overwhelmingly.** Filled-market composition:

| book | non-crypto | BTC | ETH | other crypto |
|---|---|---|---|---|
| **`Lmmsell10`** | **269 (96%)** | 9 | 2 | 1 |
| `Lmmsell8` | 0 | 11 | 11 | 0 |
| theta4 | 0 | 71 | 44 | 0 |

`Lmmsell10` is a **non-crypto book**; `Lmmsell8` and theta4 are **entirely
crypto**. On a day when BTC moved 9% and ETH 21%, that is the difference between
+$1.39 and −$18.15.

Contributions, in order:

1. **Crypto concentration** — dominant. `Lmmsell10` barely touched the assets
   that moved.
2. **Lower fill-selection haircut** — +2.3¢ versus theta4's +26¢, and not
   distinguishable from zero.
3. **Different time-to-expiry profile** — theta4 works the final 35 minutes of
   hourly ladders, where a spot move maps almost directly into settlement.
   `Lmmsell10`'s markets resolve on game and event outcomes uncorrelated with
   crypto.

### The finding that matters most for the mmsell v2 design

**`Lmmsell8` and `Lmmsell10` are not the same universe.** The treatment is
**100% crypto** (24 of 25 markets); the control is **96% non-crypto**. The
"scheduled settle" allowlist did not narrow the control's universe — it *replaced*
it, because scheduled-settlement markets are overwhelmingly the crypto hourlies.

The mmsell v2 gate compares treatment against control as though they differ only
by the entry rule. They also differ by **asset class**, and the two are perfectly
confounded: `delta.live_cents_per_contract` cannot distinguish "scheduled settle
is a better rule" from "crypto behaved differently this month". No sample size
fixes that — it is a design defect, not a power problem.

---

## 6. Historical-data limitations

1. **The unfilled control group is small.** 12 settled unfilled markets for
   theta4, 24 for `Lmmsell10`, **4** for `Lmmsell8`. theta4's result survives it;
   `Lmmsell8`'s cannot.
2. **Twin mirror coverage is partial and uneven** — `Lmmsell10` 61 of 251 filled
   markets (24%), theta4 66 of 115 (57%). Every counterfactual rests on the
   covered subset, and coverage is not obviously random.
3. **Price paths around the anchor are mostly absent** — 3 of 22 unfilled markets
   have a quote at t₀ (§4). `crypto_ladder_snapshots` covers only events near
   settlement, and quote density varies with market activity.
4. **No record of opportunities that never became a quote.** The funnel starts at
   `live_orders`; a candidate the strategy considered and skipped leaves nothing
   behind for theta (`mmsell_candidate_ticks` exists for mmsell only). So
   "qualifying opportunity → quote created / not created" is **unreconstructable**
   for theta4.
5. **Order age before fill and partial-fill structure are only partly
   recoverable.** `live_orders.created_at` and `fills.filled_at` give an age, but
   resting-order lifetime is interrupted by the stand-down drain, so ages after
   2026-08-19 are censored by an operational event rather than by the market.
6. **Momentum and volatility at entry are recoverable only for crypto**, via
   `crypto_spot_candles`; there is no equivalent for the non-crypto books, so a
   like-for-like regime control across books is not available.
7. **`Lmmsell10`'s crypto slice has no twin at all** (0 of 12), so the one
   comparison that would separate "crypto is bad" from "`Lmmsell8`'s rule is bad"
   within a single book cannot be made.

---

## 7. Telemetry worth persisting

Ordered by what each would have changed in this study:

1. **A quote-lifecycle record**: for every live order, the quote price, the touch
   at placement, and a snapshot at cancel/expiry. Turns "quoted-but-unfilled" from
   an inference off `status` into a first-class observation with its own economics.
2. **Mid/bid at fill and at fixed offsets after** (30s/1m/5m/15m), written by the
   executor at fill time rather than reconstructed from a sampling table. This is
   the single change that would make §4 answerable.
3. **A candidate-considered record for theta**, matching `mmsell_candidate_ticks`
   — the top of the funnel is currently invisible for the book that needs it most.
4. **Queue position at fill.** `live_order_queue_ticks` samples resting orders;
   the rank *at the moment of fill* is what distinguishes "we were at the front
   and got run over" from "the market simply traded through".
5. **Spot and short-horizon realized volatility stamped on each crypto entry**,
   so regime is an attribute of the trade rather than a later join.
6. **Twin mirror coverage as a first-class counter**, per cycle. A twin covering
   24% of a book is not an execution control, and nothing surfaces that today
   except a metric someone has to go and ask for.

---

## 8. Recommended follow-up experiments

**A. Crypto-versus-non-crypto within one book.** The `Lmmsell8`/`Lmmsell10`
comparison is confounded (§5). Run one book, one entry rule, split only by asset
class, with a same-instant twin on both arms. This is the experiment that would
have told us whether "scheduled settle" is a rule worth having — and it is
cheap, because it needs no new strategy.

**B. Quote-and-decline.** Place the quote, record the full lifecycle, and
deliberately decline a random subset of fills. A randomised control on the fill
itself is the only design that identifies selection cleanly, because it breaks
the correlation between "the market wanted to trade with us" and "we traded". It
costs foregone volume, not capital.

**C. Tail-model recalibration for theta, before any rearm.** theta4's live tail
ratio is **R = 4.14** (11 hits against 2.66 expected, 38 settled markets, 100%
coverage; LCB₉₉ = 1.79). The model is not mildly off; it is wrong about tail
frequency by 4×. Re-fit against realized hourly-ladder outcomes and re-validate
out of sample before the book trades real money again.

---

## 9. What this says about rearming

Reported as findings, not as a recommendation to act:

* **theta4** carries two independent problems: a **demonstrably miscalibrated
  tail model** (R = 4.14, LCB₉₉ = 1.79) and a **significant fill-selection
  haircut** (+26¢/ct, p=0.006). Its execution is fine. Neither problem is fixed
  by re-arming it, and both are visible without waiting for a v2 gate.
* **`Lmmsell8`** cannot be assessed — 4 unfilled settled markets — and its
  comparison against `Lmmsell10` is **confounded by asset class**, so the v2 gate
  as designed would not answer the question it was written for even at full
  sample.
* **`Lmmsell10`** is the one book with no detectable selection haircut, positive
  economics on both sides of the fill boundary, and profit on the day of the
  crypto rally.

---

## 10. What was deliberately not done

No strategy parameter changed. No threshold moved — in particular the 50-market
theta early-failure floor stands as pre-registered, despite theta4 currently
sitting at 38 markets with a signal that a smaller floor would already have
caught. No v2 Version created or frozen. No live restart, and no change to the
stand-down. The −$24.95 figure was not forced to reconcile with a differently
defined query.
