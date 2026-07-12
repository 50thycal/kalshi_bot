# Weather-markets research journal

A running log of every edge hypothesis we've tested on Kalshi daily-temperature
markets, what the data said, and the verdict. Newest entries at the top. The
goal: a durable record of *why* each live book exists and why each dead idea was
abandoned, so we don't re-litigate settled questions.

Conventions:
- **EV/trade** is in cents per contract, **fees on both legs** (Kalshi
  `ceil(0.07·qty·P·(1−P)·100)`), unless noted.
- "backfill" = `backfill_weather_markets` / `backfill_weather_candles` (Kalshi
  REST history, hourly candles, spring Apr–Jun, last ~48h per market — separate
  provenance from the live-collected `weather_*` tables; never mixed silently).
- Probes live in `scripts/weather_backfill_edges.py` (structural edges) and
  `scripts/weather_window_sweep.py` (entry-window EV); run via the `ops` channel.

---

## FREEZE VERDICT 2026-07-11 — UNTESTABLE (provisional, NOT a kill); the hub's ag/soft markets don't exist yet

The FREEZE thesis (`docs/FREEZE_THESIS.md`, promoted from `docs/IDEA_MODEL_20260711_run2.md` F1)
ran its probe to a verdict the same day via ops (`scripts/kalshi_freeze_study.py`, read-only public
REST + fixed exchange calendars; runs `freeze-0711-1` then `freeze-0711-2`). Thesis: Kalshi's new
commodities hub trades 24/7 while the underlying exchanges stop printing, so a contract whose
settlement window sits inside a source freeze is mechanically decided — buy the decided side while
launch-era retail still quotes it away from certainty.

**The load-bearing analytical call, made before writing the probe:** the hub settles on **Pyth**,
whose whole selling point is *continuous 24/7 pricing*. For metals/energy the underlying spot trades
~24/5 OTC, so Pyth keeps printing and the exchange-closure "freeze" is NOT real. Per the thesis's
own "when in doubt classify NOT frozen" rule, **only agricultural grains + softs get a freeze window**
(they have no meaningful weekend/overnight OTC market, so Pyth must carry a stale last print);
metals/energy are excluded from the pin measurement entirely.

**A v1 probe bug, caught on the first ops run and fixed (the same honesty discipline as PINNED
v2→v4).** v1 also scored a "SETTLEPIN" cell over metals/energy using the realized `result` as the
locked side — which is **lookahead** on a continuous source (the price is still moving), and it
manufactured a fake **+15.82¢/ct on 32k trades** — the exact "already-decided-favorite mirage" the
repo has been fooled by before (favbuy, MLBWX v1). v2 drops that cell and scores ONLY genuinely
source-frozen (grain/soft) markets; the fake edge vanished to **0 valid trades**, confirming it was
pure artifact.

**Verdict (v2, graded verbatim): UNTESTABLE — do not promote, do not close the family.** Of 3,808
settled commodity markets, **the source-frozen universe is grain 0 / soft 8** (the rest are 2,477
metal + 1,323 energy, all Pyth-continuous). Exactly **1** grain/soft market closed inside a dark
window, with **0 post-pin trades** — the mechanism was never exercised. P1 KILL / P2–P4 FAIL are all
**data-absence, not a measured flat**, so the probe reports UNTESTABLE and explicitly does NOT fire
the pre-registered "P3 fail → close the mechanical-pin family" (that rule requires ≥25 trades of real
data; there were none). P5 clean (0 red flags — nothing to flag with no tape).

**The structure enumeration (the co-deliverable that unblocks COMPIN/OPTRV) is the real finding —
the hub is metals/energy-first and the FREEZE-eligible markets barely exist even OPEN:**
| commodity | open mkts | freeze-eligible? | dominant price type |
|---|---|---|---|
| silver | 369 | no (Pyth-cont.) | threshold/close |
| gold | 359 | no | threshold/close |
| oil | 342 | no | settle/threshold/close/avg |
| natgas | 156 | no | close |
| copper | 126 | no | close |
| refined (diesel) | 43 | no | **average (41/43 — TWAP)** |
| **coffee** | **22** | **yes** | threshold |
| **cotton** | **3** | **yes** | threshold |
| **soybeans** | **2** | **yes** | threshold |

No corn, no wheat, no sugar listed at all. The thesis's whole premise — daily/short-dated ag markets
settling inside a weekend/overnight freeze — has **almost no market to trade yet**. This is a genuine
"the venue isn't ready" outcome, not evidence about the edge.

**Decision + trigger.** FREEZE is **provisionally shelved (UNTESTABLE)**, not killed. Revisit trigger:
`freeze_eligible` (settled grain/soft markets) grows into the **hundreds** — re-run the probe then. No
book built, no `BOOK_REGISTRY.md` row (nothing traded). Secondary reads for the holds queue: **COMPIN
is structurally alive** (average/TWAP-settled contracts do exist — diesel is ~all average, oil has
some) but only on Pyth-*continuous* energy, so it inherits pin15's "is the partial average known"
question on a continuous source; **OPTRV** depth couldn't be read (the v2 orderbook snapshot returned
n/a — a minor probe gap to fix before OPTRV is assessed). Probe stays allowlisted for the re-run.

## PIN15 P4 (VOL-REGIME) VERDICT 2026-07-11 — PASS; the edge holds in high vol, biggest kill-risk cleared

The Phase-A verdict flagged **vol-regime fragility as the top open risk** (the +EV sample was one
calm regime; a favorite-buy could just be harvesting a premium a high-vol regime pays back). Ran the
dedicated P4 test via ops (`kalshi_pin15_study` + a new VOLREGIME split; `pin15-volregime`, n=2,800
settled windows, min-vol lowered to 50 to widen the sample). Windows binned into quartiles by
per-window realized vol (stdev of 1-min log-returns, bps/min); P1 pin hit-rate + P2 real-ask EV
reported per quartile.

**PASS — the edge is robust across vol, and does NOT die in the wildest quartile:**
- **T-180s ask-EV by quartile:** Q1 calm +3.86¢ / Q2 +4.65¢ / Q3 +3.57¢ / **Q4 wildest +3.40¢** —
  remarkably flat, no collapse. **T-120s:** the edge *rises* with vol (Q1 +1.39¢ → **Q4 +4.64¢**),
  because calm windows are already priced to ~97¢ by T-120s while higher-vol windows keep a wider
  favorite discount (~93¢ ask) even though the pin still holds.
- **The pin holds in high vol:** a ≥5bp displacement at T-120/180s still settles the pinned way
  **96–100%** even in Q4, whose upper range (6.4–**36.9 bp/min** ≈ up to ~270% annualized) contains
  genuinely high-vol windows — not just calm ones. So the determinism (P1) is not a calm-regime
  artifact; the market underprices the near-certain favorite in high vol too.
- Thinnest cell is Q4 at T-180s (win 95.8% buying a 91.2¢ ask → ~4pp cushion) — still clears; the
  loss tail is fatter there but the EV stays +3.4¢.

**What this does and doesn't settle.** It clears the *regime* kill-risk within the observable sample
(~29 days of mid-2026, which does include real intra-sample vol spikes). It does NOT cover a
sustained crash/melt-up macro regime absent from the window — residual tail risk remains, now much
smaller, and the paper book's rolling P&L is the tripwire for it (same as every book here). The
remaining open risks are now **execution-only**: depth at the observed ask (the 1-min-candle ask is
one number, not a fillable size) and hitting the T≈120–180s entry with the worker loop. Those are
exactly what a paper book with realistic fill/depth assumptions is built to test.

**Decision:** P1 ✅, P2 ✅ (in-sample), P4 ✅ — the pre-registered gates are met and the biggest
fragility is cleared. **Advance to a `pin15` paper book** (forward-test real fills at a tightened
T≈120–180s poll, held to settlement) rather than more probing. Correlation caution stands: it's a
favorite-buy (the family `tfav` died in on the hourly ladders); the material difference — live
spot-pin selection, now validated to hold across vol — is why this one is worth forward-testing.

## PIN15 PHASE-A VERDICT 2026-07-11 — P1 PASS, P2 pass-in-sample-but-fragile, P3 (SPIKEFADE) FAIL

The PIN15 thesis (`docs/PIN15_THESIS.md`, promoted from the 15-min-crypto idea-model run) ran its
Phase-A probe twice via ops (`scripts/kalshi_pin15_study.py`, read-only public REST). Venue: Kalshi
15-minute crypto up/down (`KXBTC15M`/`KXETH15M`). Sample: 1,600–2,400 settled windows, ~8–17 days,
one calm vol regime. Two runs: first with candle-mid quotes (`pin15-phaseA-1`), second charging the
**real taker ask** at a contemporaneous (≤1-min) quote (`pin15-phaseA-ask`) — the honest execution cost.

**STRUCTURE (confirmed):** `KXBTC15M` is a `greater_or_equal` threshold whose `floor` strike =
"Target Price", and that target ≈ the prior window's settle ≈ spot at window open. So it IS a
"does spot end ≥ where it started" direction bet, settled on the 60-second average of the CF index.

**P1 — PASS, decisively.** The outcome de-randomizes early and calibrates sharply. At **T-120s**, a
spot displacement of **≥ +5bp → settles Up 98–100%**; **≤ −5bp → 0–2%**; only the ±2bp cell is a
coin flip. **~76% of windows are already ≥5bp displaced (i.e. decided) by T-60s** and by T-180s.
Actionable latency: the signal exists minutes out, not in the un-catchable final seconds.

**P2 — PASS IN-SAMPLE but FRAGILE, and the tradeable window is 2–3 min out, NOT the close.** Buying
the drift-favored side at the **real ask**, held to settlement, net of worst-case fee:
**T-180s +3.9¢/ct** (ask 93.3¢, win 98.3%), **T-120s +3.6¢** (ask 95.1¢, win 99.8%), decaying to
**T-60s +1.1¢** (ask 97.3¢, win 99.4%). Split-half OOS consistent (+3.5/+4.3 at T-180s); BTC and ETH
both positive; spreads tight (ask only ~0.7–1¢ over mid, so the mid→ask haircut was small). **The
fragility:** this is buying a ~93–95¢ favorite with only ~**3–4pp of win-rate cushion** over
breakeven, in a **single calm vol regime**. In a high-vol regime the same 5bp displacement would flip
far more often (2-min BTC σ is ~2–3bp calm but ~10bp+ in a normal regime), which could compress or
invert the edge — the ~2% loss tail costs ~93¢ each. **P4 (robustness across vol regimes) is
therefore UNTESTED** — the whole sample is one regime; this is the theta-in-one-vol-regime caution again.

**P3 — FAIL as pre-registered.** The predicted mechanism (SPIKEFADE: fade a last-10s spike the 60s
average mutes; edge RISES as T→0) is **wrong**: SPIKEFADE windows are **rare (2.8%)** and **weak
(settle followed the average only ~75%)**, and the edge **FALLS** as T→0 (the favorite prices toward
100¢). The real edge is plainer and obs-family: **the drift-favorite is mildly underpriced ~2–3 min
before close** and the market closes the gap into settlement. This reshapes any book (enter at
T≈120–180s, not the final seconds) but does not kill it — the decision rule gated on P1∧P2, both of
which pass in-sample. **UPBIAS:** realized Up-rate 50.5–50.7% (no structural directional bias).

**Verdict + correlation caution.** Promising and rare-for-this-repo (a clean in-sample +EV), but NOT
a green light to live. Two hard caveats before any build commits real effort: (1) **vol-regime
fragility** (P4 untested — the biggest kill risk); (2) it is a **favorite-BUY**, the same family
`tfav` died in (−3.6¢ on the hourly ladders) — the material difference is that here the favorite is
picked by a live spot-pin, not a static price band, but the correlation/skepticism is live. Probe stays
allowlisted. Next step is a fork (see thesis RESULTS): a cheap **vol-regime robustness re-run** to
attack P4 on history, and/or a **`pin15` paper book** that forward-tests real fills/depth at a
tightened T≈120–180s poll and naturally samples the next regime.

## THETA4 EDGE LOOSENED 10c→6c 2026-07-11 — pre-registered decision enacted (0 trades at edge=10)

theta4 (the mult=2.0 fat-tail revival test, the one live variant trading while the rest of theta
is collect-only) made **zero trades in ~24h+** at its `edge=10c` bar — confirmed live via ops
(`gate-check-0711`: theta4 has no `paper_trades` rows at all). Nothing was ≥10c overpriced *after*
a 2× tail-fatten. That 0-trade outcome was the exact pre-registered decision point in the strategy
loop (run #32/#33): **"loosen theta4's edge from 10c → ~6c so it actually trades and we can measure
whether the 2x-fattened model's tails are calibrated; if it STILL barely trades or trades
negative/miscalibrated, conclude the fat-tail revival is impractical and leave theta fully shelved."**

Enacted that here (operator-directed): `config.py` `theta_variants` → `theta4:...,edge=6` (from
edge=10). This is a **paper-only** change to one collect-only-exempt variant; the rest of the theta
family stays shelved (`theta_collect_only=True`, `theta_live_variants="theta4"` unchanged), and the
ladder/spot collectors are unaffected. **The pre-registered n≥80 gate is unchanged and now applies
to the edge=6 sample:** KEEP only if per-trade > 0 AND realized-tail-hit rate ≤ 1.25× modeled; if it
fails both (or still barely trades), the fat-tail revival is impractical and theta is fully dead —
stop reviving. Interpretation caveat carried forward: that nothing cleared a 10c bar after a 2×
fatten is itself weak evidence the base model's tail miss really was ~2×, but it yields no tradeable
signal, hence loosen-or-conclude rather than declare victory. No test asserts the production edge
value (the theta tests use synthetic variant strings), so this is a pure config-default change;
it takes effect on the next Railway redeploy (merge to the default branch). The loop will read the
new theta4 n at its next pass.

## PINNED + DECAY VERDICTS 2026-07-10 — both idea-model theses KILLED at the probe; no books built

The two theses pre-registered in `docs/IDEA_MODEL_20260710.md` ran to verdict the same day via
ops (`scripts/kalshi_pinned_study.py`, `scripts/kalshi_decay_study.py` — read-only public REST,
no DB/collector). Both graded verbatim against their pre-registered thresholds. Neither
promotes; per the north star, two clean cheap ruling-outs, zero paper bleed.

**DECAY (deadline-hazard premium on "by-date" markets) — KILLED, decisively.** Full universe:
16k settled events → 2,050 by-date markets → 1,818 with candle history. Shorting YES∈[5,20]¢ at
T-14d: **−19.97¢/ct at n=152, win 70%** (P1 KILL bar was <+1¢; this isn't close). The hazard
signature also failed (T-7 −0.69 / T-14 −19.97 / T-28 −6.23 — not monotone), split-half
disagreed, and the tail test failed spectacularly (worst day −$32 vs +18¢ median winning day).
The category cut explains it: **Science/Tech "will X ship/happen by date" markets resolved YES
94% of the time in the short cohort (EV −86.6¢)** — in this era the by-date "hope" is
under-priced, not over-priced; Politics also negative (−4.95). Only Elections (+2.6) and
Entertainment (+4.3) were mildly positive, far below a book. The by-date premium is fair-or-
inverted insurance; **close the family** (and note it as a caution against any future
"sell hope" extension of mmsell into by-date one-offs).

**PINNED (post-pin discount on slow-settlement-source markets) — do not promote (P2 fail, P1
grey).** Final clean run (v4): pooled post-pin EV **+1.80¢/ct on 119 real trades** (P1 needs
≥+3¢, kill <1.5 → GREY), and it does NOT beat the pre-pin favorite-buy control (+2.23¢, P2
FAIL) — the "post-pin discount" is mostly just generic favorite pricing, the dead tfav shape.
The core cells: **weekly AAA gas markets post-pin = +0.02¢ (perfectly efficient)**; CPI
single-print markets −0.21¢ (efficient, institutional). The one cell that cleared the bar —
**post-jobs-print trades at +43.4¢/ct — is only 36 trades** and is logged as a residual
observation, NOT promotable under the registered rule; if ever revisited it needs its own
pre-registration and a release-time audit (JOLTS/ADP vs 8:30 BLS contamination risk).
Probe-construction history, for honesty: v2 scored 0 trades (trade tape sends
`yes_price_dollars`, not the cents field); v3 produced 8,193 pinned-side-loss red flags —
bucket-midpoint path reconstruction at weekly granularity understates volatility (false pins
on KXAAAGASW) and natural-gas futures settles are not a glacial index. v4 pins only
KXAAAGAS{D,W,MAX,MIN} against the KXAAAGASD daily path (running-max-rate reach guard ×1.5 +
2 bucket-halfwidths) and restricts the release family to single-print markets closing ≤8h
after the release; it ran with **zero red flags**, so the verdict stands on a sound probe.

Meta-lesson reinforced: the staleness family's surviving shape is *observation-pinning on
markets where nobody watches the source* — and this run shows even backwater gas/econ quotes
converge once the answer is public. The inattention window that powers weather `con` does not
generalize off-weather at tradeable size. Both probes stay allowlisted for future re-runs.

## THREE NEW PAPER BOOKS 2026-07-10 — data-cut variants of the survivors (pre-registered)

Opus build session. After the PR #22 cleanup left the portfolio with no positive earner, built
one new, data-justified paper book per surviving family — each an A/B against its parent, each
with a pre-registered falsifiable gate. Grounded in the full paper history (per-cell queries,
n far larger than the original decompositions), not vibes.

**mmsell3 (yes 5-10c band) — pure config (`mmsell_variants += mmsell3:lo=5,hi=10`).** The
per-yes-price-band history (n>1,700 settled `mmsell`): 5-10c nets **+2.2c/trade at 96% win over
n=378** (also the highest-volume band), 10-15c only +0.7c, and 15-20c is NEGATIVE (-2.2c).
mmsell1 (5-20) and mmsell2 (10-20) both dilute the 5-10c winner with the flat/negative 10-20c
cells — which is why they compressed to ~+0.7c as n grew. mmsell3 isolates the sweet spot.
  PRE-REG GATE: at n>=150 settled, KEEP only if per-trade > +1.5c AND >= mmsell1/mmsell2;
  otherwise the "cheapest is best" read was small-n and mmsell3 is dropped.

**theta4 (fat-tail revival test) — `theta_variants += theta4:hi=20,ttemax=35,mult=2.0,edge=10`,
activated past collect-only via the new `theta_live_variants="theta4"`.** The theta shelve
post-mortem found the SpotModel underprices realized tails by ~1.4-2.6x (median ~1.85x). theta4
attacks that root cause directly: `mult=2.0` fattens the model tails ~2x (to match realized),
and it sells ONLY tails still >=10c overpriced AFTER fattening, in the cheap 3-20c band / final
35min (theta1's better window). New mechanism: `theta_live_variants` lets ONE named variant
trade while `theta_collect_only` keeps the control + theta1/2/3 snapshot-only — this is the
"fresh pre-registration, not a quiet re-enable" the shelve doc required; the ladder/spot
collectors keep running underneath.
  PRE-REG GATE: at n>=80 settled, KEEP only if per-trade > 0 AND realized-tail-hit rate
  <= 1.25x modeled (i.e. the 2x fatten fixed the calibration). If it fails BOTH, theta's failure
  is NOT a simple tail-scale error and the family is fully dead (stop reviving). Expected to be
  SPARSE (double gate: 2x-fatter model + 10c bar) — n will accrue slowly; that's fine for paper.

**weather_concity (con restricted to AUS/CHI/NYC) — new parallel book + `weather_con_allow_cities`
knob.** The all-time by-city con history (n=23-63/city) shows the con edge is concentrated by
CITY, not window (all windows ~flat): winners AUS **+11.7c/trade** (n=63), CHI +5.8c (n=31), NYC
+5.7c (n=23); losers LAX -11.6c, DEN -9.5c, PHIL -6.1c; MIA ~flat. NB the 10-day window (run #29)
disagreed for some cities (MIA/PHIL looked positive there) — small-window noise; the all-time cut
is the trustworthy one. weather_concity rides the SAME consensus pick as `weather_con` but only
enters for the allowlisted edge cities (City.code AUS/CHI/**NYC** — New York's code is NYC, not
the 'NY' series suffix). Runs alongside the unrestricted con as a live A/B.
  PRE-REG GATE: at n>=120 settled, KEEP (and consider retiring the all-city con) only if
  weather_concity per-trade > +3c AND clearly beats the full con book; if it doesn't beat
  all-city con, the by-city split was noise and concity is dropped.

All three: paper-only, deployed via config (one Railway redeploy enacts them), tests added
(theta live-variant-under-collect-only, concity allowlist in/out, mmsell3 parse). The loop query
now surfaces `weather_concity` separately; mmsell3/theta4 auto-surface as non-weather books.

## PERPS SURVEY 2026-07-09 — zero-fee crypto perps NOT on the market API; discovery gap, no probe

Phase-2 Task 3 (optional, no clock): does Kalshi's mid-2026 zero-fee crypto **perpetual
futures** product expose a pullable price history, and is there perp↔spot / perp↔ladder basis
structure that could survive *normal* fees? Surveyed with `scripts/kalshi_perps_survey.py`
(read-only). **Result: no perp series is reachable via the public `/trade-api/v2` event/market
endpoints** — 76 open Crypto events scanned, none perp-marked; candidate series tickers
(KXBTCPERP, BTCPERP, …) all resolve to 0 markets. The perps evidently live on a separate
product/API surface, so there is **no price history to pull through the event-contract channel**
and the basis/coherence read can't run. Per the thesis, **do not promote** (the zero-fee promo
would make any cost gate misleadingly easy anyway). Next step *if* revisited: find Kalshi's
perp-specific endpoint/feed, then a funding/basis staleness probe gated on normal fees — not a
series-name guess against the event API. Folds slate N4/N5/N15 into this one open discovery item.

## MLBWX VERDICT 2026-07-09 — weather-total staleness KILLED (P2 −1.5¢); do not promote

Pre-registered MLBWX thesis (docs/IDEA_MODEL_20260709.md): on Kalshi's MLB run-total market,
an imminent-rain nowcast should reprice the total *down* (rain suppresses scoring / risks
postponement) faster than the casual sports flow, leaving a stale lagging quote to take.
Probed with `scripts/kalshi_mlbwx.py` (read-only backtest, 45 days).

**A v1 of the probe tested the WINNER market and defined the trade direction from the SETTLED
price — a look-ahead bug that manufactured a fake +5.5¢ "edge" (you always profit if you pick
the direction the price ended up going).** Caught it in the per-row table (net was positive for
BOTH the home and away contract of every game — the tell). Rebuilt to the correct instrument
and a no-lookahead design:
- **Instrument = KXMLBTOTAL** (Over-K run total), where rain carries a *signed* prior; the
  winner market carries none (rain doesn't say which team wins).
- **Direction fixed by the weather signal** (short the Over on rain), never by the outcome;
  P&L is the price-path capture. Robust away/home matchup split (handles 2-letter codes).

**Data (all confirmed liquid, so P4 is not the blocker):** KXMLBTOTAL ≈6000 markets, contract
spread median **1.0¢**, 91% ≤5¢; 468 games mapped to parks, 374 open-air; 63 open-park games
with a rain event (≥1mm near game time), 6 postponed/suspended.

**Verdict (n=44 rain-affected games with a tradeable ATM Over contract):**
- **P1** median 5-min completion = 0.00 → PASS (the quote barely moves in 5 min) — but this is
  *not* exploitable because there's no profitable direction:
- **P2** short-Over capture to settlement = **−1.5¢/ct** → **KILL** (bar +4¢; kill <2¢). The
  rain→under prior *does* pay on the big rainouts (BAL 07-09 +42¢, CHC rainouts) but is offset
  by games where rain didn't suppress scoring (CLE −46¢), and the postponement cases settle on
  rule-dependent void/reschedule terms. Net: no capturable edge.
- **P3** pre-game-only capture = −0.5¢ → no directional level edge either.
- **P4** 44/48 rows clear the 5¢ spread filter → PASS (liquidity is fine).

**Decision rule was P1 ∧ P2 ∧ P4 → not met (P2 KILL) → do not promote; close the family.**
Kalshi's MLB total market prices weather efficiently at the tradeable horizon (or the
rain→runs link is too noisy to trade). This is the cheap clean ruling-out the thesis
anticipated — ~44 games, zero production code shipped. The weather infra was reused read-only
(Open-Meteo park precip); nothing about the temperature books changes.

## XGAME VERDICT 2026-07-09 — WC lead-lag SHELVED (P2 KILL); paper book not built

The `xgame_tape_study` ran to verdict on the World Cup knockout tape (matched pairs through
the 07-06/07-07 quarterfinals — the games where both venues actually collected: belgium,
portugal, spain, colombia, egypt, switzerland; ~104 shock events pooled). Graded against the
pre-registered predictions in `docs/IDEA_MODEL_20260704.md` (XGAME), two independent runs
(`xgame-study-1`, `xgame-verdict-0709`) agree:

- **P1** (follow%>55 AND same-bar<40): follow≈60% but **same-bar≈57%** → **GREY** — moves are
  contemporaneous, not cleanly led.
- **P2** (median net follow-through ≥4¢): **−2.0¢** (gross +1.0¢, fees eat it) → **KILL**.
- **P3** (PM→K exceeds K→PM by ≥10 pts): 60% vs 59%, gap +1 → **FAIL** — symmetric, i.e. both
  venues follow a shared third feed rather than one leading the other.
- **P4** (median exploitable window ≥20s): 600s → PASS (windows are wide, but there's nothing
  profitable to exploit in them once P2 fails).

**Decision rule** was: paper book `xgame` only if **P1 ∧ P2 ∧ P3**. Not met → **do not build the
`xgame` paper book; shelve.** P1 is GREY (not a clean fail), so per the rule the lead-lag
*family* is not fully ruled out — only this WC instance + entry. MLB (year-round, liquid) is the
family's only remaining testable home; see the collector-retarget note below.

**Operational note:** the newest knockout games (07-09 france/morocco, and the 07-10/07-11
upcoming games) show Kalshi rows but **0 PM rows** — the Polymarket leg is no longer matching
current games, and `game_matches_active=0` on the live recheck. So the "most liquid knockout
sample" the Phase-2 order hoped to add before the Jul-19 WC close is effectively already not
flowing; the verdict rests on the 07-06/07-07 sample, which is sufficient for the P2 KILL.

**Collector retarget to MLB (Phase-2 Task 1b) — NOT DONE, deliberately.** The Phase-2 order
framed this as a one-line config change (add KXMLBGAME to `xgame_series` + an MLB PM tag). The
board survey showed it is not: Kalshi MLB markets are liquid (KXMLBGAME ~1878, KXMLBTOTAL
~6000, 1¢ median spreads), but **Polymarket's MLB per-game markets are titled "TeamA vs.
TeamB"**, not the WC "Will `<team>` win on `<date>`?" that the collector's `PM_WIN` regex
(`kalshi_bot/xgame/pm.py`) requires — so the matcher would produce **zero** MLB pairs, and
Kalshi's abbreviated subtitle ("Chicago C") wouldn't norm-reconcile with PM's "Chicago Cubs"
either. A real retarget = a new PM "TeamA vs. TeamB" parser + a team-name/code reconciliation
layer across `pm.py` and `tracker.py` — a multi-file collector change, not config. Given the WC
lead-lag was just **KILLED** and P3's "symmetric / shared third feed" is a *structural* reason
that transfers to MLB (both venues watch the same game feed), we chose (2026-07-09, with the
operator) **not to invest in the rewrite** — it would feed a dead edge. Revisit only if a new
mechanism (not naive lead-lag) motivates in-play MLB tape.
## PORTFOLIO CLEANUP 2026-07-09 — shelve theta, kill tfav + wcprop + xgame book; con diagnosed by-city

Fable/opus session acting on the run #21–#29 loop findings (`docs/STRATEGY_LOOP_STATUS.md`).
Four books turned off, one book diagnosed. All paper — the value is stopping the attention/paper
bleed and encoding the verdicts in the repo so they aren't re-litigated. Collectors stay on.

**theta — SHELVED (collect-only), not deleted.** The model-anchored crypto tail-seller failed
its pre-registered "positive AND calibrated at n≥60" gate on every book (control + theta1/2/3).
The decisive evidence was a *live round-trip*: the control peaked at **+$15.53 (n=495, run #27)**
during a calm 3-day stretch, then gave the entire gain back to **−$0.07 (n=542, run #29)** in two
windows. The calibration drill-down (run #27) showed realized tail-hit rates **1.4–2.6× the
model** on every book (control 21.8% vs 15.6% modeled; theta2 14.0% vs 5.3%) — the SpotModel's
empirical remaining-window return distribution undersamples regime shifts, so it systematically
under-prices the tails it sells. A parametric fix can't rescue a distribution-shape error, which
is why theta1 (band/window surgery), theta2 (thresholds-only), and theta3 (widened tails + higher
bar) all also failed. Implementation: **`theta_collect_only=True`** (new flag) keeps
`ThetaTracker.run_once` refreshing spot + writing the model-priced `crypto_ladder_snapshots`
(the labeled dataset a future fatter-tail model rebuilds from) while skipping ALL entries.
`theta_enabled` stays True so the collector runs. Reviving the book requires a genuinely new
model (Student-t/EVT tail fit, or a market-implied prior with divergence gating) under a fresh
pre-registration — not another live variant. theta1's +6.0¢/trade (n=196) is the only datum
arguing for a revival and is explicitly NOT a reason to keep it trading now.

**tfav — KILLED.** The taker-buy-favorites mirror of theta crossed its pre-registered n≥150 gate
**negative** (−3.6¢/trade at n=210) after three straight window whipsaws (−$11.31 → +$3.99 →
−$7.54). The favorite side of the favorite–longshot bias doesn't net positive here either;
combined with mmsell's ~breakeven maker-sell result, the bias is not harvestable on these hourly
crypto ladders from either direction net of realism. `tfav_enabled=False`. (Spot/ladder
collectors are theta's, unaffected.)

**wcprop — KILLED.** The World-Cup winner-ladder lag book was armed and cycling every ~10 min
through the ENTIRE knockout stage (15+ matched games settling) and opened **zero** trades: no
`KXMENWORLDCUP` rung ever moved ≥3¢ within 45 min of a match settling on a liquid, non-rail
rung. The post-match repricing either completes inside one cycle or doesn't happen — no lag is
harvestable at ride-along cadence. This confirms the offline probe's P1-kill (winner ladder
efficiently priced) forward. `wcprop_enabled=False`.

**xgame book — SHELVED (collector left on).** `xgame_tape_study` on 19 matched WC games returned
a decisive verdict: **P2 KILL** (median net follow-through −2¢ after costs; gross only +1¢) and
**P3 FAIL** (PM→Kalshi 58% vs Kalshi→PM 59% — *symmetric*). The symmetry is the real finding:
neither venue leads because both simply track the match itself (a shared "third feed" = the live
game), so there is no cross-venue lead-lag to trade. The pre-registered rule shelves the book on
P2 fail and rules out the lead-lag family on the symmetry. `xgame_book_enabled=False`; the
`xgame_enabled` tape collector is left running to finish out the tournament. The book had 0 trades.

**weather con — DIAGNOSED (kept on, do not scale yet).** con went net-negative cumulatively for
the first time (+$1.46 → −$1.24, runs #27→#29). The by-city breakdown (last 10 days, settled)
shows the drawdown is **concentrated in hard-to-forecast cities, not uniform**:
| city | n | win% | P&L | ¢/trade |
|---|---|---|---|---|
| MIA | 24 | 46% | **+$1.06** | +4.4 |
| PHIL | 12 | 42% | **+$1.50** | +12.5 |
| CHI | 10 | 20% | −$0.59 | −5.9 |
| NY | 6 | **0%** | −$0.94 | −15.7 |
| DEN | 16 | **0%** | −$3.13 | −19.6 |
| AUS | 29 | 52% | −$3.17 | −10.9 |
| LAX | 19 | 21% | −$3.23 | −17.0 |
The winners (MIA, PHIL) are stable coastal/eastern climates; the bleeders are Denver (0% win —
altitude/mountain regime), LAX (marine-layer microclimates), Austin, NY. **Recommended next
fable step (not done here — small per-city n):** cross-check per-city forecast skill in
`weather_forecast_outcomes`, then restrict con to the cities where it has real edge (MIA/PHIL/…)
and drop or shrink DEN/LAX/AUS/NY — a "concentrate where the edge is" move, the same lesson theta
and mmsell taught. Do NOT scale con until this is done. Left running as-is for now.

**mmsell — LEFT ON as a zero-attention data book.** All three bands sit at ~breakeven (pooled
~+0.3¢/trade, n≈2,700), oscillating around zero — no edge net of realism, but not a bleeder.
Kept running for data; not promoted, not a live candidate.

## XGAME MATCHER FIX 2026-07-05 — wrong PM tag ("soccer" = club games) matched 0; WC tags → 13

The XGAME collector logged `kal_games=14 pm_games=169 matched_new=0` for two days — deployed
and seeing both venues, but pairing nothing. Diagnosed with a live read-only probe
(`scripts/xgame_match_debug.py`, which reproduces the collector's exact `(day, team)` key
extraction): the code was fine; the **config tag was wrong**. `xgame_pm_tags="soccer"` pulls
Polymarket's CLUB soccer markets (145 club teams, dates spanning Mar–Jul) — **zero team
overlap** with the World Cup national teams KXWCGAME lists (france, morocco, argentina, …).
The WC per-game "Will `<team>` win on `<date>`?" markets live under the **`fifa-world-cup` /
`2026-fifa-world-cup`** tags. Switching the tag took the live intersection **0 → 13** matched
(day, team) pairs (14 Kalshi keys, minus the `tie` line PM doesn't run). Fix: one-line default
`xgame_pm_tags = "fifa-world-cup,2026-fifa-world-cup"` + a regression test asserting the tag
targets the World Cup, not club soccer. The matcher/normalization code was already correct
(the existing tests use a fake PM client, so they never exercised the real tag) — the tag must
match the sport/tournament of `xgame_series`. On the next deploy the collector should match ~13
WC games and begin filling `game_market_matches` / `game_tape_snapshots`; watch the
`xgame collector` log line (`matched_new` > 0) and the loop's `data:xgame_*` rows.

## WEATHER PRUNE 2026-07-04 — keep only `con`; all other weather books are confirmed bleeders

Fable-session decision from the per-book forward P&L (settled paper, NOT legacy):

| book | n | total | c/trade | verdict |
|---|---|---|---|---|
| **con** | 239 | **+$9.83** | **+4.1** | KEEP — the only +EV weather book |
| favband | 41 | −$3.65 | −8.9 | prune |
| pm | 158 | −$6.15 | −3.9 | prune (book only; DATA stays for con) |
| obs | 171 | −$6.35 | −3.7 | prune (book only; DATA stays for con) |
| cwin | 130 | −$6.81 | −5.2 | prune (already dormant) |
| dist | 669 | −$26.55 | −4.0 | prune |
| fav | 1196 | −$49.41 | −4.1 | (already off) |
| nws | 1146 | −$63.83 | −5.6 | (already off) |
| cal | 1137 | −$72.03 | −6.3 | prune |

Pruned via config defaults (`weather_strategies="none"` + a new `weather_strategy_list`
"none"/"off" sentinel that returns `[]`; `weather_dist_enabled`/`weather_city_window_enabled`/
`weather_favband_enabled`/`weather_obs_entry_enabled` → False; new `weather_pm_book_enabled`
→ False separating the pm BOOK from the pm DATA). **Critical: the data collectors that feed
`con` stay ON** — forecasts, ensembles (`weather_ensemble_enabled`), observations
(`weather_obs_enabled`), and Polymarket (`weather_polymarket_enabled`); only the losing
*entry books* stop. Existing open positions on pruned books hold to settlement (startup
abandon keeps `weather*`), then no new entries. Net effect going forward: the weather program
trades ONLY `con` (+4.1c/trade), ending ~−$4-5/settlement-batch of paper bleed + the API load
of six dead books.

## MMSELL REVISION 2026-07-04 — forward data OVERTURNS the tape: the edge is in CHEAP longshots, not the mid-band

Fable-session decomposition of 445 settled mmsell paper trades by yes-price-sold band —
the naive proxy is breakeven (+1.3c/trade pooled) because it AVERAGES a real edge and a
real drag:

| yes-sold | n | c/contract | win% |
|---|---|---|---|
| 5-10c | 113 | **+2.69** | 96% |
| 10-20c | 151 | **+3.60** | 91% |
| 20-35c | 121 | **−1.50** | 74% |
| 35-50c | 57 | **−4.35** | 60% |

This is the **opposite** of the raw kalshi_mm tape backtest (which rose with price, 20-35c
best). The resolution: hold-to-settlement harvests the **favorite-longshot bias** — cheap
longshots are the most overpriced relative to their tiny true probability, so selling them
and holding nets the premium at 91-96% win; mid-price contracts aren't overpriced enough to
cover their bigger loss-when-hit (the tape measured ALL maker fills incl. two-way flow, a
different object than sell-and-hold). The control's 5-40c band drags in the losing 20-40c
cells. Shipped two pre-registered revision books next to the untouched `mmsell` control
(same scan/orderbook, band only): **mmsell1** (5-20c, the broad cheap sweet-spot) and
**mmsell2** (10-20c, the single best band). Decision rule (in-sample decomposition ⇒ needs
OOS): keep a variant only if it beats the control forward at ≥~150 settled with per-trade
P&L clearly > 0; the parallel books ARE the out-of-sample test. `MMSELL_VARIANTS` config.

## IDEA-MODEL 2026-07-04 → Phase 2 built: TFAV / WCPROP / XGAME pipelines + pre-registered probes (verdicts pending)

*(2026-07-04. Pre-registered theses, predictions and decision rules in
`docs/IDEA_MODEL_20260704.md` — written BEFORE any validation ran; thresholds must not
be re-scoped post-hoc. This entry logs the Phase-2 data-pipeline/probe build only;
verdicts land here once the probes run on real samples.)*

The idea-model run screened 18 candidates → 3 promoted, and all three now have their
data pipelines + ops-runnable probes in place (run order = cost-to-verdict):

- **TFAV** (crypto hourly favorite-buy, the mirror of theta's parked side-finding):
  NO new collection needed — the theta collector already snapshots the FULL ladder
  (every price band ≤90min to settlement, model P attached), so 65-90¢ favorites are
  accumulating in `crypto_ladder_snapshots` since 2026-07-03. Probe
  `scripts/kalshi_favbuy_study.py` (ops: `kalshi_favbuy_study`) grades P1-P4:
  unconditional ~0 / model-filtered ≥+3¢ with split-half agreement / final-hour
  concentration / |corr| < 0.4 vs a theta-rule sim on the same events. Labels come
  from the Kalshi settlement archive (public REST), quotes+model from live snapshots.
- **WCPROP** (WC match-result → tournament-winner-ladder propagation lag): no
  collector needed — public 1-min candlesticks. Probe `scripts/xmarket_wc.py` (ops:
  `xmarket_wc`) detects result-known time from the match market's own pin (fallback
  close_time), measures winner-contract repricing completion at +1/+5/+15min and the
  net residual entering at the +5min quote (P1 <70% / P2 ≥+3¢ / P3 survives spread≤5¢).
  Runnable NOW against the live tournament.
- **XGAME** (in-play PM→Kalshi lead-lag on scoring shocks — the repo's standing #1
  frontier): the blocker was in-play tape collection, now built. New ride-along
  collector `kalshi_bot/xgame/` (COLLECT ONLY, no trading; `XGAME_*` config, default
  on) matches Kalshi per-team game markets (KXWCGAME) to Polymarket same-team/day
  markets by (day, normalized team) — precision over recall, ambiguous keys dropped,
  FULL clobTokenId stored — and polls BOTH venues' trade tapes (Kalshi
  `/markets/trades` with min_ts high-water marks; PM data-api, overlap+dedup) into
  `game_market_matches` / `game_tape_snapshots` (new tables, migration
  `f3a4b5c6d7e8`; separate provenance from all weather/crypto tables). Trades carry
  venue timestamps, so the probe builds ~10s bars regardless of poll cadence. Probe
  `scripts/xgame_tape_study.py` (ops: `xgame_tape_study`) grades P1-P4: follow%>55 &
  same-bar<40 / median net follow-through ≥4¢ / PM→K exceeds K→PM by ≥10pts / median
  exploitable window ≥20s.

Ops note: the three probes are allowlisted in `scripts/ops_runner.py`; refresh the
`ops` branch from the default branch after merge so the channel picks them up. The
XGAME collector starts filling tables on the next Railway deploy — watch the
`xgame collector` log line (kalshi_games/pm_games/matched tell you immediately if the
PM question-format match is off).

**First live runs (same day, via ops — all PROVISIONAL, tiny n):**
- `xmarket_wc` (21d, all 48 teams mapped, 80 decisive events): only **2 measurable
  rows** — most candidate rows died to `below_min_move` (140: group-stage results
  genuinely don't move title odds ≥3¢; the tradeable version of this thesis is
  knockout-round) and `missing_quotes` (86: winner-ladder candle minutes often lack a
  two-sided close → added a trade-price-mid fallback for pin/completion; quotes still
  required for entry/spread). Provisional read on n=2: P1 lag exists (5% completion@5m)
  but P2 residual **−2.45¢ → KILL-shaped** — the two group-stage repricings were too
  small to clear fees. Re-run in the knockout rounds before any verdict.
- `kalshi_favbuy_study` (62k snapshots, 52 events, ~1.5 days of collection): pipeline
  + settlement-labeling work. Unconditional favorite-buy shows **+6.9¢ — flagged
  SUSPICIOUS by the probe itself** (the pre-warned already-decided-favorite artifact +
  one calm regime + small n, the classic mirage). Model filter fired only n=2 (+21¢,
  meaningless). P4 not computable yet. Let snapshots accumulate ≥2 weeks before
  reading anything into it.
- `xvenue_game_probe` (format check): PM per-game markets confirmed as
  `'Will Paraguay win on 2026-07-04?'` (~$4M/game volume) — the xgame matcher regex
  fires as designed; PM trade-tape keys match the collector's parsing. **Live format
  discovery: KXWCGAME `close_time` is a far-future settlement DEADLINE (game Jul 6 →
  close Jul 21), not the game end** — the collector's poll window was re-keyed to the
  ticker-derived game day, and settled close_times ≈ finalization (hours late), which
  is why `xmarket_wc` anchors on price-pin detection rather than close_time.

*(2026-07-04.)* First ~21h live: theta −$13/40 settled vs +4.4¢ backtest. Read-only
decomposition found the miss is CONCENTRATED, not uniform: **38/40 entries were KXBTC range
buckets at 20-40¢** (model 19% vs realized 37% — the trailing distribution under-weights
center mass, so "overpriced" flags on near-money buckets were model error) and **40-55min
entries carried the losses** (−11.6¢/ct vs positive at 10-40min — matching the tape's
edge-lives-late structure). 10-20¢ trades were +17.8¢/ct (tiny n). The unselected
snapshot-calibration set (spot-at-close labels) shows far tails perfectly calibrated and
3-40¢ *threshold* tails if anything over-priced by the model in this calm regime — so a
global tail-fattening fix is wrong. Shipped three pre-registered revision books next to the
untouched control (same scan/model, gates only): **theta1** (band 3-20¢ + tte 10-35m),
**theta2** (theta1 + thresholds-only), **theta3** (wide config + edge≥12¢ + mult 1.25).
Decision rule: ≥~60 settled/book, keep only positive-P&L books whose realized tail-hit ≤
modeled; all negative → shelve the family. Full detail in `docs/THETA_THESIS.md`.

## THETA book — hourly crypto ladders: naive tail-selling ~0, MODEL-FILTERED tail-selling +EV (built)

*(2026-07-03, Claude's own book — full thesis + pre-registered predictions in
`docs/THETA_THESIS.md`; probe `scripts/kalshi_theta_study.py`, ops-runnable.)*

Tested Kalshi's recurring hourly crypto ladders (KXBTCD/KXBTC/KXETHD/KXETH — 24
settles/day/series, deep 3-40c tails, retail lottery flow) on ~3-7 days of settled
history, 87.5k tape trades, and a Coinbase 1-min spot model (empirical remaining-window
return distribution, no lookahead):

- **Naive tail-selling at the quotes: ~0 EV** (T-30 gap +0.7c, n=494) — the posted
  quotes are calibrated on average. No unconditional band edge. (Same lesson as every
  price-only weather probe.)
- **The realized maker-SELL flow is +5.2c/contract net of worst-case fees** (2.09M
  contracts, split-half +5.11/+5.32), and by minutes-to-expiry the edge is entirely
  **inside the final hour**; >60min out it inverts negative. Maker-BUY mirror: -9.3c.
- **A spot-vol model separates dead tails from live ones at the same price**: selling
  only model-overpriced tails (mid - 100·P_model >= 5c) at the ask = **+4.44c/contract**
  (n=114); model-fair tails = -1.52c. Strongest: 10-20c overpriced -> +13.2c (win 2.6%
  vs 15.4 implied) — quote staleness after spot moves, the obs-lag family.
- Side-finding parked: hourly 65-90c favorites ran ~9-11c UNDERpriced (small n) — a
  possible future buy-the-favorite probe.

**Built the same day**: `theta` paper book (`kalshi_bot/theta/`) riding the weather/live
cycle like mmsell — collects 1-min spot (`crypto_spot_candles`) + near-settlement ladder
snapshots with the model probability attached (`crypto_ladder_snapshots`), and sells
model-overpriced 3-40c tails at the ask (buy NO at no-bid) ONLY at 10-55min to
settlement, qty 5, capped 3/event, hold-to-settlement. Caveats: the model split's n=114
covers one vol regime — the paper book is the real out-of-sample test (~100s of
trades/week); fills assume our ask is hit (mmsell's known limitation, partially derisked
because the tape measures realized passive fills).

## Standing verdict / themes

**Kalshi's temperature ladder is efficient on everything derivable from price
history.** Overround, persistence/autocorrelation, calibration across the price
range — all priced. The only positive-EV we've found comes from *information the
market is briefly slow to incorporate* (obs-confirmed late entry once the day's
real thermometer reading is in; cross-market signal from Polymarket) and from
*city-specific entry timing* (the city × window map). Everything that only uses
the ladder's own prices has come back dead or sub-fee.

Live books currently running: `fav` (control), `nws`, `cal`, `pm`
(Polymarket-cross), `cwin` (per-city high windows), `obs` (running-extreme late
entry). The legacy h12 window is flagged in the DB and excluded from the PnL
report.

### The bucket-probability edge model — NOW LIVE as the `dist` book
Built and shipped (`weather_dist` / `weather_low_dist`). How it works:
- **Engine:** `kalshi_bot/weather/distribution.py` — `member_bucket_prob` (Gaussian
  kernel, sigma = forecast error beyond ensemble spread) → `model_bucket_probs`
  (blend GFS+ECMWF → per-bucket P) → `best_bucket_by_edge` (buy the single bucket
  whose model prob most beats its ask, net of fee, by ≥ `weather_dist_min_edge_cents`).
  The live counterpart of the offline grader in `scripts/weather_model_check.py`
  (math duplicated there so the ops script stays self-contained).
- **Inputs:** reads the latest stored ensemble for (city, date, kind) from
  `weather_ensembles` (collected live by `OpenMeteoEnsembleClient`); self-gates —
  no ensemble → no trade. Enters per entry-window like fav/nws/cal/pm.
- **#6 baked in:** `weather_dist_sigma` default 1.5 °F keeps the model distribution
  tighter than the market's overdispersed ladder — the edge is precisely that the
  ensemble-grounded distribution is sharper than the crowd's.
- **Config:** `weather_dist_enabled` (true), `weather_dist_sigma` (1.5),
  `weather_dist_min_edge_cents` (5). Watch its settled P&L vs `fav` in the PnL table.
- **Still crude (kept as controls):** `nws`/`cal` buy only the single point-forecast
  bucket; `dist` is the real distribution model.

---

## First real-money live test ($1, Jun 14) — buy path CONFIRMED; 2 parser bugs fixed

Ran a tiny live round-trip via the env channel (relaxed spread + a fresh entry window to
force entries on near-close low favorites). Outcome:

- **The buy path works end-to-end on real money.** 3 BUY orders placed and FILLED on Kalshi
  (DEN low @82¢, CHI @99¢, LAX @88¢, 1 contract each), with real `kalshi_order_id`s captured
  and reconciled to `filled`. The env-channel control, place_order, fill reconciliation, and
  the risk gate (it correctly blocked `SPREAD_TOO_WIDE` until relaxed) all validated live.
- **Two reconciliation parser bugs — only real API data could reveal them — found & fixed:**
  1. **Fills**: Kalshi sends `yes_price_dollars`/`no_price_dollars` (dollar strings),
     `count_fp` (fixed-point), `fee_cost` (dollars) — not `yes_price`/`count`/`fee`. Fixed
     (reuse `price_to_cents`/`_to_count`). Confirmed: DEN @82¢/qty1/$0.0104 fee.
  2. **Positions**: fields are `position_fp` (signed FP), `market_exposure_dollars`,
     `realized_pnl_dollars` — not `position`/`market_exposure`/`realized_pnl`. The old parser
     left `realized_pnl` null, **silently disabling the daily-loss circuit breaker**. Fixed.
- **Known remaining issue — SELL/exit orders rejected** (`invalid_parameters`, 400). The
  exit-order param format is wrong for Kalshi. NOT needed for the validated books (they're
  hold-to-settlement), but must be fixed before using live TP/SL/break-even exits. The sl=1
  in the test was only a contrivance to force a round trip.
- **Lesson:** the demo/shape probe caught the read shapes (orders/balance) but the fill/
  position element fields could only be confirmed with a real fill. Always do a tiny live
  buy-and-reconcile before trusting the parsers.

Aftermath: kill switch back ON, config restored (spread 5, windows 20/14/8, exit settlement);
3 tiny low-favorite positions (~$2.69) left to settle (also validates the settlement path).

### Follow-up fixes (both confirmed)
- **Settlement path CONFIRMED on real money.** Added `/portfolio/settlements` reconciliation
  (settled positions vanish from get_positions, so realized losses could escape the daily-loss
  breaker). Verified against the test settlements: CHI B64.5 **+$0.005** (bought 99¢, won),
  DEN B54.5 **−$0.8304** (bought 82¢, lost = 0 − 0.82 − 0.0104 fee) — both computed correctly
  and now feed `live_realized_pnl_today`. Settlements shape (revenue cents, *_total_cost_dollars,
  fee_cost, market_result) confirmed via the probe before parsing.
- **SELL/exit bug FIXED.** A raw `action="sell"` yes order returns Kalshi `invalid_parameters`.
  Exits now close a YES position by **BUYing the opposite (NO) side** at `100 − yes_bid` —
  reusing the proven buy path; Kalshi nets the opposing position (same P&L as selling at the
  bid). Unit-tested; verified-by-construction (identical mechanics to the working entry buy).
- Net realized cost of the whole live test ≈ **−$0.83** (CHI +0.005, DEN −0.83; LAX pending).
  Worker restored to clean baseline: BOT_MODE=weather, KILL_SWITCH=true, live fully disarmed.

### Round-trip test #2 (buy → ~1min hold → close) — mechanics CONFIRMED; exit edge case
Sped cycles to 60s, forced fresh h18 entries, sl=1 to close. Result:
- **Full round trip works end-to-end:** DEN-T69 entry **buy YES @85¢ (filled)** → ~15s hold →
  exit **buy-NO @17¢ (FILLED)**, closing the position. The buy-NO close (the fix) executes on
  real money. ✓
- **Exit reliability edge case:** 2 of 3 exits (CHI high B69.5, CHI low B58.5) were rejected
  `400 invalid_parameters` on payloads STRUCTURALLY IDENTICAL to DEN's (differ only by ticker +
  no_price) — so it's not a code bug; likely a transient market-state condition for those
  buckets at that instant. A duplicate DEN exit got `409 order_already_exists` (Kalshi's
  client_order_id idempotency caught a fast-cycle retry race — a non-issue at the normal 300s
  cadence). **Takeaway: the buy-NO close is proven but not yet 100% reliable across markets;
  harden it (retry/fallback, confirm before relying on live TP/SL) before using non-settlement
  exits.** The validated books are hold-to-settlement (no exits), which is fully proven.
- Aftermath: 2 open CHI positions (@45, @71) left to settle; baseline restored, fully disarmed.

### Exit hardening (Phase 6) — robust close, not a new method
Research confirmed buy-NO-to-close is Kalshi's *canonical* close (it nets the pair → credits $1);
the CHI rejections were validation/market-state, not a wrong method. So the fix is robustness:
- **Position snapshot is the source of truth** (reconcile runs before manage_exits each cycle):
  an exit is "done" only when Kalshi shows the position flat; otherwise re-attempt.
- **Dedup bug fixed:** `live_exit_order_exists` counted `rejected` as committed, permanently
  blocking re-attempts. Split into `live_exit_in_flight` (non-terminal only) + `count_exit_attempts`
  (ladder/cap); `open_live_positions` no longer hides a position behind a rejected exit.
- **Price-escalation ladder:** base marketable buy-NO → slippage-buffered limit on re-attempts →
  optional best-effort market order (flag-off; market fields unconfirmed) → hold to settlement.
- **409 order_already_exists → success** (it landed); unique `client_order_id` per attempt
  (`exit:{strategy}:{ticker}:{n}`) avoids self-409. **Partial fills** sized to remaining qty.
  **Full Kalshi error body + payload logged** on rejection (the hook to finally RCA the CHI 400).
  **Bounded attempts** (default 3/day) then CRITICAL + hold to settlement.
- Config: `live_exit_slippage_cents` (0), `live_exit_use_market_fallback` (false),
  `live_exit_max_attempts` (3). 11 new exit tests. Still inert until tp_sl mode + live switches on.

### Exit verification #2 — hardening WORKS; root cause = bucket-market closes rejected
A live `tp_sl` round trip confirmed the hardened mechanics: re-attempts (`exit:...:2`, `:3`),
price escalation, **409 order_already_exists treated as success**, full error body captured,
bounded attempts. But the **close still fails on weather bucket markets**, and now we know why
the error body is uninformative: Kalshi returns a GENERIC `{"code":"invalid_parameters",
"message":"invalid parameters"}` with **no field detail** — even fully logged.

**The empirical pattern across all live tests is the real RCA:** *closing* a weather range-bucket
market (`...-B##.#` tickers) via API limit order is rejected `invalid_parameters` in BOTH
directions — `action=sell/side=yes` (test #1) AND `action=buy/side=no` (tests #2/#3) — while
*buying YES to open* the same bucket works fine, and the one THRESHOLD market (`...-T##`,
DEN-T69) accepted a buy-NO close. So it's specifically *closing the mutually-exclusive bucket
markets* that Kalshi rejects; the close method/code is not the problem.

**Implication:** live early-exit (TP/SL/break-even) is NOT viable on the weather bucket markets
with current knowledge (Kalshi's error gives no specifics; docs are 403-blocked). The reliable
path for the weather books is **hold-to-settlement** (the validated strategy — needs no closes).
Open question for later: try a market order, or ask Kalshi support, for the bucket-close param.

Ops note: the Railway env API had transient read-timeouts during cleanup; the timed-out writes
still landed (verified BOT_MODE=weather, KILL_SWITCH=true). Minor follow-up: mirror_entry should
also treat 409 as success (benign — Kalshi dedups on client_order_id). Baseline fully restored.

### The close was FIXABLE — wrong format, not the market (app screenshot proved it)
A screenshot of the Kalshi app cashing out a *bucket* position via "Slide to Sell" disproved the
"bucket markets can't be closed" theory. The working **pykalshi** SDK revealed the real issue: it
sends `action="sell", side="yes"` with **`count_fp` + `yes_price_dollars`** (strings) and **no
`type`/`no_price`** fields. Our close used integer `no_price`/`yes_price` + `type:"limit"`, which
Kalshi rejected for sells (generic `invalid_parameters`). Buys tolerated the integer format (entries
filled); sells did not — hence the asymmetry.
- **Fix:** `_place_exit` now SELLS the YES position like the app does — `action="sell", side="yes",
  count_fp="N.00", yes_price_dollars="0.NN"` at the bid (escalated re-attempts sell lower to force a
  fill; market fallback = `type="market"` sell). All exit tests updated to the new shape; green.
- **Live verification blocked:** Railway's env API had repeated read-timeouts, so the live tp_sl
  test couldn't be reliably configured (writes land but the config sequence kept getting cut). The
  fix is well-grounded (the app + a working SDK use exactly this format) and unit-tested, but a clean
  live confirmation is still pending. Worker left disarmed (weather, kill switch on).

### DEFINITIVE close finding — the exit now FIRES, but the API rejects BOTH closes on bucket markets
A clean live `tp_sl` test (LAX-B72.5, a favorite, ~11h to close so the market was definitively
OPEN) plus full payload evidence from `live_orders` finally settled the question — and overturned
the two prior theories ("sell-YES dollar format is the fix" AND "buy-NO works, CHI was just
closed-market"). Both were wrong. What the evidence actually shows:
- **The exit machinery is FIXED and now fires reliably.** Two bugs had prevented the close from
  ever being attempted on LAX: (1) the entry `409 order_already_exists` was mis-recorded as
  `rejected`, corrupting position tracking; (2) `open_live_positions` keyed off the (corrupted)
  entry row instead of the Kalshi position. Fixes: 409→`submitted` (success), and
  `open_live_positions` now driven by the **Kalshi position snapshot** (managed even if the entry
  row is corrupted). After deploy the worker correctly fired attempts `:1…:4` with retry,
  price-escalation, bounded attempts, and snapshot-as-truth — all working.
- **Kalshi rejects every close form we can build on a range-BUCKET market, even when OPEN:**
  - `sell-YES` integer limit `{side:yes,type:limit,count:1,action:sell,yes_price:68}` → `400
    invalid_parameters` (26JUN13).
  - `sell-YES` dollar `{side:yes,action:sell,count_fp:"1.00",yes_price_dollars:"0.74"}` → rejected
    (LAX 20:29–31, **market open**).
  - `buy-NO` integer limit `{side:no,type:limit,count:1,action:buy,no_price:30}` → rejected (LAX
    20:39, **market open**). This is the one that kills the "closed-market" theory: the LAX bucket
    was open for ~11h and buy-NO STILL failed.
- **The only close that has ever filled is buy-NO on a THRESHOLD market** (`-T##`): DEN-T69 buy-NO
  @17 executed. So the split is **threshold (`-T##`) closeable via buy-NO vs range-bucket
  (`-B##.#`) rejected in both directions** — a market-type distinction, not an order-format bug.
- **The app CAN close a bucket position** (user screenshot, "Slide to Sell"), so an accepted API
  form exists, but Kalshi's generic `invalid_parameters` (no field detail) + 403-blocked docs +
  SDKs that abstract the wire format mean we can't yet derive the exact bucket-close parameters.
- **Code state:** `_place_exit` reverted to the canonical **buy-NO** netting close (proven on
  threshold markets; the original Phase-6 design), marketable at `no_price = 100 − yes_bid`
  (+slippage on escalation), integer `count`/`type:"limit"`/`no_price`. All tests green.
- **Operational implication (unchanged & important):** the validated, edge-bearing weather books are
  **hold-to-settlement** and need NO closes — live trading is viable today on that basis. Live
  early-exit (TP/SL/break-even) on the bucket books is the one piece still blocked on the unknown
  bucket-close API form. Worker disarmed to the safe baseline (BOT_MODE=weather, KILL_SWITCH=true,
  LIVE_ENABLED=false); the open LAX position (~$0.80) holds to settlement.

### Order-group investigation — DISPROVEN; bucket==threshold structurally (read-only probe)
Added `scripts/kalshi_market_probe.py` (read-only, public Kalshi market-data, browser UA to
clear Cloudflare — no auth, no orders) and compared the LAX bucket that REJECTED closes
(`KXHIGHLAX-26JUN14-B72.5`) against the DEN threshold that ACCEPTED a buy-NO close
(`KXHIGHDEN-26JUN14-T69`). They are structurally identical for order purposes:
`market_type=binary`, `can_close_early=true`, `fractional_trading_enabled=true`,
`price_level_structure=linear_cent`, `price_ranges` step `0.01`, `response_price_units=usd_cent`,
`notional_value=$1`. The only diffs are economically irrelevant to orders (`strike_type`
between vs greater; `settlement_timer` 1800 vs 300). There is **no `order_group` field** on
markets or events, so order-groups are not involved.
- **Decisive:** the DEN market that closed fine is ALSO in a `mutually_exclusive=true` event —
  identical to the LAX bucket. So "mutually-exclusive bucket can't be closed" is FALSE; mutual
  exclusivity does not block closing. Buckets are closeable in principle.
- **So the LAX close rejections were not structural.** Two concrete culprits from the payload +
  spec evidence: (1) the sell-YES *dollar* attempts omitted the spec-required `type` field
  (`count_fp`/`yes_price_dollars` with no `type` → invalid_parameters); (2) every market is
  `fractional_trading_enabled` (sizes/OI come back as `*_fp`, prices as `*_dollars`), so the modern
  order path likely wants the fractional fields TOGETHER (`count_fp` + `*_price_dollars` +
  `type:"limit"`), not a half-integer/half-dollar mix. The one residual puzzle is the LAX buy-NO
  integer `no_price:30` (type=limit) rejection — same format that FILLED on DEN-T69 — which has no
  structural cause and is most likely a transient state/liquidity/balance edge at that instant.
- **Open spec question:** the authoritative `CreateOrderRequest` (official OpenAPI client) confirms
  `type` is required for limit orders, `Cent` = integer cents, and selling the held side is the
  documented close; docs.kalshi.com is Cloudflare-403 to WebFetch but GitHub-raw spec/model files
  are readable. Next definitive step is either capturing the app's real Slide-to-Sell request, or
  aligning the close to the full fractional format and re-testing once on a bucket.

### Fractional sell-YES retest — REJECTED too; all spec-derivable close forms exhausted
A fresh $1 round trip (h11 LAX favorite) cleanly tested the fractional sell-YES close (the app's
field shape WITH the spec-required `type`): `{action:sell, side:yes, type:limit, count_fp:"1.00",
yes_price_dollars:"0.XX"}`. The entry filled (and the entry-409→filled handling worked), then the
close fired as `exit:weather_fav_h11:...:1` and was **REJECTED `400 invalid_parameters`** — same as
every other form. So the fractional-format hypothesis is also wrong.

**Definitive close matrix on the weather RANGE-BUCKET markets (`-B##.#`, market OPEN):**
| close form | result |
|---|---|
| sell-YES integer (`type:limit, yes_price`) | REJECTED invalid_parameters |
| sell-YES fractional (`type:limit, count_fp, yes_price_dollars`) | REJECTED invalid_parameters |
| buy-NO integer (`type:limit, no_price`) | REJECTED invalid_parameters |
| buy-NO integer on a THRESHOLD market (`-T##`) | FILLED |

Every order matches Kalshi's published `CreateOrderRequest` spec, and the bucket market is
structurally identical to the threshold market that closes (same `market_type=binary`,
`mutually_exclusive`, `can_close_early`, `fractional_trading_enabled`, `linear_cent`). So the
blocker is neither order format nor order-groups nor market structure that we can see. The Kalshi
APP closes these buckets (user screenshot + the user's own app close of a LAX position, which
appeared in our fills as trades the bot never makes), so an accepted form EXISTS — we simply cannot
derive it from the spec/SDKs (generic error, 403-blocked docs). **The only remaining definitive path
is capturing the app's actual Slide-to-Sell request** (web devtools). Until then: the validated
weather books are hold-to-settlement (no closes needed) and live-tradeable on that basis; live
early-exit (TP/SL) on bucket markets stays blocked. Reusable read-only `kalshi_market_probe.py`
added for future structural checks. Worker disarmed to baseline; entry-window override reverted.

### EXHAUSTIVE close sweep — EVERY API order shape is rejected on bucket markets (conclusive)
A close-format discovery ladder (rejected orders are harmless no-ops that leave the position open,
so one deploy cycles many shapes) tried every order form the Kalshi API supports against a live,
OPEN LAX range-bucket position. **All rejected `400 invalid_parameters`:**

| # | close order shape | result |
|---|---|---|
| 1 | sell-yes limit, integer `yes_price` | ❌ |
| 2 | sell-yes limit, fractional `count_fp`+`yes_price_dollars` | ❌ |
| 3 | sell-yes limit + `sell_position_floor:0` | ❌ |
| 4 | sell-yes limit + `reduce_only:true` | ❌ |
| 5 | sell-yes **market** + `sell_position_floor:0` | ❌ |
| 6 | buy-no limit, integer `no_price` | ❌ |
| 7 | buy-no **market** + `buy_max_cost` | ❌ |

Meanwhile **buys to OPEN the same bucket fill fine**, and a **buy-no close fills on a THRESHOLD
market** (`-T##`). So this is not our order format, not a missing field, not order-groups, not
market structure (the bucket and threshold markets are byte-for-byte identical in the public
objects: `binary`, `mutually_exclusive`, `can_close_early`, `fractional_trading_enabled`,
`linear_cent`). **Conclusion: the Kalshi public order API does not accept ANY close/reduce order
on these range-bucket weather markets from this account, by any derivable shape.** The app closes
them (user's own app close appeared in our fills), so an accepted path exists via the app's
(privileged/internal) flow that the documented REST order endpoint does not expose to us.

**Decision/operational impact:** live early-exit (TP/SL/break-even) on the weather BUCKET books is
**not achievable via the API** with current knowledge — this is a Kalshi-side restriction, not a bot
bug. The validated, edge-bearing books are **hold-to-settlement** (need no closes) and are fully
live-tradeable. Everything ELSE in the live path is proven on real money: entry, marketable fill,
409→filled idempotency, reconcile (fills/positions/settlements), settlement P&L, the daily-loss
breaker, and exit *firing* (retry/escalation/snapshot-as-truth/bounded attempts). To unblock live
bucket exits, the remaining avenues are: capture the app's actual close request, or ask Kalshi
support why a spec-correct close on a range market returns `invalid_parameters`. The
`_exit_candidate` ladder + `kalshi_market_probe.py` remain in the tree for when a format is found.

### SOLVED — bucket close works via Kalshi's v1 user-scoped order endpoint (live-verified)
Captured the web app's Slide-to-Sell request (browser devtools) and it explained everything: the
app does NOT use the v2 `/trade-api/v2/portfolio/orders` endpoint the bot used — it posts to the
**v1 user-scoped endpoint** `POST /v1/users/{user_id}/orders` with a different schema:
```json
{"market_id":"<uuid>","user_side":"yes","side":"no","order_action":"sell","order_type":"market",
 "time_in_force":"immediate_or_cancel","count_fp":"1.00","price_dollars":"<100-yes_bid>",
 "sell_position_capped":true,"post_only":false,"expiration_unix_ts":0,"max_cost_cents":0}
```
That's why every v2 close (sell-yes / buy-no, all variants) was rejected `invalid_parameters` — the
v2 endpoint simply doesn't accept closes on these range-bucket markets; the v1 endpoint does.

Implemented + LIVE-VERIFIED on real money (a $1 LAX bucket round trip): entry filled → the v1
close FIRED and FILLED (fills table: `no sell, qty 2, @37` closing the position for **+$0.05**;
not settlement — that market didn't close until the next day). Key details learned the hard way:
- **Auth:** the bot's RSA API-key auth IS accepted on `/v1` (signs the full v1 path; same keys).
- **market_id:** the v1 order needs the market UUID, which the v2 objects don't expose and
  `/v1/markets/{ticker}` 404s — it lives on the **v1 event's nested markets**
  (`/v1/series/{series}/events/{event}`), matched by `ticker_name`.
- **price_dollars** must be the live NO-side price (`100 − yes_bid`, +slippage to cross); the
  app's `0.01` was specific to its ~99¢ position. An IOC that doesn't cross is canceled unfilled.
- **async fill:** the v1 IOC returns `status:"pending"` + an `order_id` and fills ~a cycle later;
  the fill carries that order_id, so reconcile resolves the order (`filled`/`canceled`).
- **`sell_position_capped:true`** closes the whole position safely (it even swept an older stuck
  position in one shot).

So the live early-exit (TP/SL/break-even) path now works on the weather BUCKET books, not just
hold-to-settlement. Full live loop proven on real money: entry → fill → reconcile → **API close →
fill → flat**. Worker disarmed to baseline after the test. (Security: the capture's request headers
contained a session cookie/CSRF/WAF token — ignored, never stored; operator advised to log out.)

### Fractional sizing — BUY confirmed live, but ONLY via the v1 endpoint (v2 rejects it)
Goal: spend an exact dollar amount (a "$3 position" = $3 of contracts, not `floor($3/price)`),
since the integer `count` floor leaves positions short of the target. Markets are
`fractional_trading_enabled` and `count_fp` accepts 0.01-contract increments.

**Hard live finding:** the **v2** `/portfolio/orders` endpoint rejects EVERY fractional
(`count_fp`) buy with `400 invalid_parameters` — verified across `yes_price` 91→99 (all fully
marketable, so it is NOT a pricing/marketability issue). v2 simply has no fractional support.
Fractional is a **v1-only** capability: the same `POST /v1/users/{user_id}/orders` endpoint the
close uses accepts `count_fp`. Rebuilt the isolated probe BUY as a **v1 fractional market buy** of
YES (mirroring the v1 close body: `order_action:"buy", side:"yes", user_side:"yes",
order_type:"market", count_fp, price_dollars=through-ask, max_cost_cents=cap`). LIVE-VERIFIED on
real money: MIA B93.5 filled at `count_fp 1.55` (avg 97¢, ~$1.50) — a true fractional position,
not rounded to a whole contract.

**Close side, fractional:** the v1 close already sizes to the exact fractional remainder
(`count_fp = quantity_fp`, tracked via the new `positions.quantity_fp`); unit-tested. In the live
probe it was NOT demonstrated end-to-end because the MIA position kept getting closed before
`_probe_close` ran. Diagnosing that uncovered a real bug (below).

### CRITICAL bug found + fixed — cycle-wide transaction rollback duplicated REAL orders
A controlled retry of the fractional round trip exposed it: a single `$1.50` probe buy on
`KXHIGHMIA-26JUN16-B92.5` became **3 real orders / ~12 contracts / $4.68** ("3 trades" on the
app) while only **ONE** `live_orders` row persisted (`quantity 4`). A 4-contract order cannot
fill 12 → multiple real orders were placed but their rows vanished. Root cause: `_run_live_cycle`
wraps the WHOLE cycle (`reconcile → manage_exits → run_probe → manage_open_positions →
tracker.run_once`) in ONE `session_scope` transaction. An order's intent row was only durable at
cycle-end commit, so a downstream error rolled it back **after** the order already hit Kalshi —
and the dedup / one-shot guards (which read that row) then re-fired a DUPLICATE real order next
cycle. This also explains the earlier "phantom" closes (a bot order whose row was rolled back).
**Fix:** in every order-placement path (`mirror_entry`, `_place_exit`, `_probe_buy`)
`session.commit()` the `pending` intent row **immediately, before the POST**, so it survives any
later cycle rollback and the dedup guard can never be fooled. Regression test
`test_intent_committed_before_post_survives_rollback_no_dup_order` simulates a post-POST rollback
and asserts no duplicate order fires (fails without the commit). The strategy's own once-per-
window entries did NOT duplicate (only the probe's every-cycle retry hammered the guard), so the
live DEN/LAX books were unaffected. The bot's fractional close is the same proven v1 path, sized
to the remainder.

**Fractional live ENTRY — now wired to v1 (`LIVE_FRACTIONAL=true` is safe).** `mirror_entry` was
reworked: when `live_fractional` is on it builds the shared `_v1_buy_body` (the same v1 fractional
MARKET-buy vocabulary the probe/close use) and POSTs via `create_v1_order`, instead of the v2
`count_fp` order Kalshi rejects. Sizing is `count_fp = dollars / price` capped by depth / risk /
`max_order_size`; the order is priced a couple cents through the ask with a cost cap; reconcile
leaves the async-filled v1 buy `submitted` (committed → dedup holds) and tracks the real size from
the position snapshot. Fail-closed: if `live_user_id`/`market_id` can't be resolved the entry is
skipped (logged), never silently mis-placed. So `LIVE_FRACTIONAL=true` now spends an exact dollar
cap (a "$3 position" = $3 of contracts) on the live books. Integer path (`live_fractional=false`)
unchanged.

**Reconcile hardening — no duplicate on an indeterminate-but-filled v1 entry.** A v1 order is
never visible in the v2 orders feed, so reconcile used to mark any `pending`/`unknown` order it
couldn't find there as `not_landed` — which would drop it out of the dedup set and let the next
cycle re-fire a DUPLICATE entry if the transient POST had actually landed. Now reconcile checks
Kalshi's freshly-fetched fills/positions first (`_executed_on_exchange`): any position or
same-action fill for the ticker → the order is resolved to `submitted` (committed → dedup holds);
only with NO execution evidence is it `not_landed` (a genuine non-landing, retry allowed).
Conservative by design — it favours "don't double-enter" over "never miss a retry". Regression
tests cover both directions (filled→submitted→no-dup; no-evidence→not_landed).

### Live trade log — DEN/LAX h-window favorites closed at ~99¢ (Jun 15, real money)
The two cross-validated favorite books filled at their windows and ran to ~99¢ (both winners),
then were closed early (operator call) via the bot's v1 buy-NO close rather than held to
settlement. Both positions went flat with matching no-sell fills, and the close intent rows now
PERSIST (post atomicity fix) — DEN's row resolved to `filled`; LAX's filled too (position flat +
6@1 fill) but its order row was cosmetically mislabeled `not_landed` (a known v1-close reconcile
gap: v1 orders aren't matched by `client_order_id` in the v2 orders feed, so a transient/async
close can be mis-resolved — harmless, the position snapshot is the authority for management):
| book | bucket | entry | exit (≈) | qty | gross P&L |
|------|--------|-------|----------|-----|-----------|
| `weather_fav` DEN h-win | `KXHIGHDEN-26JUN15-B81.5` | 48¢ | ~99¢ (no-sell @1¢) | 6 | ≈ +$3.0 |
| `weather_fav` LAX h-win | `KXHIGHLAX-26JUN15-B70.5` | 49¢ | ~99¢ (no-sell @1¢) | 6 | ≈ +$3.0 |

Net ≈ **+$6 gross** (minus entry+exit fees) on the two $3 favorite entries — the cross-validated
HIGH-favorite edge paying off live. (A separate 0.38-contract `LAX-B72.5` residual from an earlier
exit-sweep test had already hit the 3-attempt exit cap, so the bot holds it to settlement — ~$0.24,
immaterial.) This is also the first clean live demonstration that the bot's own close fires AND
leaves an audit record now that intent rows commit before the POST.

## Exit & sizing studies (live settled trades)

### Inverse view — rank by BACKFILL, check live (the trustworthy direction)
`weather_strategy_compare.py --top`. Ranking the favorite by the robust backfill sample
(n≥15/cell) and showing live alongside flips the picture from the live-first ranking:

- **The best backfill favorite cells are almost all HIGHS** (12 of the top 14; only NYC h20
  and PHIL h14 lows appear). The favorite edge the history supports lives in *highs*, not
  lows — the opposite of the live-first ranking, confirming the low-book live profit was
  small-sample luck.
- **Cross-validated winners (both samples positive, high backfill win%):** late-window (h8)
  highs in stable cities — DEN h8 (+3.8¢/68 backfill 96% · +3.7¢ live), MIA h8 (+3.6¢/100% ·
  +1.0¢), AUS h8 (+2.9¢/95% · +10.6¢), CHI h8 (+1.2¢/95% · +10.0¢) — plus LAX highs across
  windows (LAX h20 +3.3¢·+11¢, LAX h14 +3.3¢·+33.8¢) and NYC h14 (+1.5¢·+20.5¢).
- **Sign agreement: 11 of the top 14 backfill cells are also positive live.** The 3
  disagreements are all *early-window* continental highs (DEN h20, CHI h20, DEN h14: backfill
  + but live − on n=4) — early entry is less reliable (the high hasn't formed), and the *late*
  (h8) versions of the same cities cross-validate positive. Window matters: late > early for highs.

**Go-live implication (validated from both directions):** the defensible first live book is
**late-window (h8) high favorites in stable cities (DEN/MIA/AUS/CHI/LAX)** — high historical
win rates (95-100%) AND positive live — not the low favorite. Live per-cell n is still tiny
(1-5), so treat magnitudes as noisy; the SIGN agreement is what matters.

### Backfill vs live cross-validation — the low-book edge does NOT survive (caution!)
`weather_strategy_compare.py` replays the favorite three ways: backfill (Kalshi REST
history, n~350-480/cell), live realized paper P&L (n~17-28/cell), and best TP/SL on the
live trades. **The low books contradict between samples:**

| kind/window | backfill (large n) | live (small n) |
|---|---|---|
| low h20 | **−6.4¢ (468)** | +12.7¢ (19, 89%) |
| low h14 | −3.7¢ (346) | +5.7¢ (18) |
| low h8 | −6.6¢ (354) | +2.2¢ (17) |
| high h20 | +1.3¢ (481) | −4.2¢ (28) |
| high h8 | −0.9¢ (230) | +6.1¢ (18, 100%) |

The robust 25×-larger backfill says the **low favorite LOSES ~4-7¢**; the tiny recent live
sample says it's our best winner (+12.7¢). This is the small-sample/recent-regime trap in
action — the live "low fav h20 = best strategy" headline does **not** cross-validate. Trust
the backfill: do NOT seed live trading with the low favorite on the strength of the live
numbers alone.

**The only sign-consistent (both samples agree) positive signal is highs in stable
marine/subtropical cities:** high LAX backfill +3.2¢ / live +22.4¢; high MIA backfill +1.0¢
/ live +18.1¢. Everywhere else the two samples disagree (low cities: backfill all negative,
live all positive; DEN high +4.6¢ backfill but −2.6¢ live). TP/SL barely moves anything —
hold is best in 5 of 6 window cells (only high h20 benefits: tp=20 → +2.9¢), echoing the
exit-sweep finding. **Implication for go-live: the cross-validated candidate is high
LAX/MIA, not low fav.** Gather more live low-book settlements before trusting that edge.



### Best-strategy breakdown (live settled, by city × window × book)
`weather_pnl.py --best`. Robust picks (n ≥ 15, ranked by P&L/trade):
**low fav h20 = +12.7¢ (n19, 89%)** — the standout; then low nws/cal h14 +7.7¢ (n15),
high fav h8 +6.1¢ (n18, 100%), low fav h14 +5.7¢ (n18). Finer city cells are all n≈4 —
noise, not picks.

**City pattern worth noting (n 7–9):** the HIGH books are net negative *pooled*
(fav −2.2¢, nws −1.3¢) but strongly positive in **stable-climate cities** — high nws
MIA +33¢ (n9, 100%), cal MIA +28.6¢, cal CHI +25.9¢, fav LAX +22.4¢. Miami/LA highs are
low-variance (subtropical / marine layer) so forecasts nail them; the high-book edge is
**city-dependent**, echoing the #3 city×window result. Candidate: gate the high books to
stable cities (MIA/LAX/CHI), drop the volatile ones — needs more n before acting.

### Exit dynamics on the profitable (low) books — HOLD-TO-SETTLEMENT WINS
Replayed 158 settled low-book trades (`low_fav/nws/cal/pm` + `high_pm`) through their
recorded 15-min bid paths under a TP/SL grid, a **break-even stop** (arm at +gain,
then exit at entry if it falls back), and a **size/fee** sweep. `weather_exit_sweep.py`.

- **Hold beats every exit rule:** hold = **+5.3¢/trade**; the best TP-only is +3.6¢
  (−1.6¢ vs hold), and every stop-loss is brutal (hold/5¢SL = −10.9¢, −16¢ vs hold).
  Any SL gets whipsawed out of trades that dip intraday but settle YES.
- **Break-even also loses:** best BE config (arm 12¢ / TP 15¢) = +2.4¢, still −2.9¢
  vs hold; BE-only −0.9 to +0.5¢. Same reason — it exits near-certain winners on a
  transient dip back to entry.
- **Why:** the low-book edge is *outcome certainty* (thermometer lag — the overnight
  low is already locked, the bucket settles YES ~93%), not price momentum. Exiting
  early on a price wiggle only forfeits the settlement payout. **Verdict: keep the
  weather books hold-to-settlement; do not add TP/SL/BE to them.**
- **Size/fee:** per-contract P&L is +5.29¢ (qty 1) → +5.78¢ (qty 5) → +5.87¢ (qty
  100). Kalshi fees scale with qty, so size only amortizes the `ceil` rounding —
  a one-time ~+0.5¢ from qty 1→5, then flat. The real fee lever is *price* (fee =
  0.07·P·(1−P), tiny near 0/100), which the low favorite already enjoys. Trading ≥5
  contracts is a free ~0.5¢, but size is NOT the cost fix; holding + high-price entries are.

## Backfill structural-edge hunt (Apr–Jun history, 964 complete events)

**Scorecard (all 9 probes complete):** #3 city×window VALIDATED (→ live `cwin`);
#6 distribution overdispersion REAL (→ model calibration feature, not a book);
#2b mean-reversion REAL but sub-fee (→ model feature). Everything else priced or
dead — #1 overround, #2 persistence, #4 longshot, #5 momentum/convergence, #7 W→E
lead-lag (real physics, priced), #8 diurnal coupling (real physics, priced), #9
liquidity (mid honest even when thin). **Conclusion: Kalshi temperature markets
are remarkably efficient on everything derivable from price/public data — the only
positive EV is timing (`cwin`) and last-mile information lag (`obs`, `pm`), plus
sharpening the forecast model with #6.** Next build: promote the offline
distribution model into a live book (see "Build backlog" above).

### #9 — Liquidity-conditioned mispricing — REFUTED (mid is honest even when thin)
Is mispricing concentrated in illiquid buckets? At h12, grade each bucket's mid
vs its realized win-rate, binned by bid/ask spread and by candle volume.

- **The mid is well-calibrated in EVERY liquidity bin.** By spread: ≤2¢ → mid_err
  −0.7, 3–5¢ → +1.6, 6–10¢ → −1.2, >10¢ → −0.8 (even the widest-spread buckets win
  42% at a 42.9¢ mid). By volume: 0 → −0.4, 1–50 → +0.4, 51–500 → −1.2, >500 → −0.1
  (even zero-volume deep longshots, avg mid 4.6¢, win 4% — perfectly priced). Thin
  books do **not** create stale/sloppy mids.
- **Illiquidity is pure cost, not edge.** yes/no EV degrades monotonically as the
  spread widens: YES −2.4¢ (≤2¢ spread) → −11.3¢ (>10¢ spread); NO likewise. The
  spread is wide *because* there's no edge, and crossing it bleeds you. Thin
  buckets are the most expensive to touch, not the most profitable.

**Verdict:** the "sloppy pricing in thin markets" hypothesis fails outright —
Kalshi's resting quotes are honest even in the corners (zero-volume tails, >10¢
spreads) where sloppiness is most expected. Probe: `--analysis liquidity`.

### #8 — Diurnal-range coupling — strong coupling, PERFECTLY priced (no trade)
Within a city/day the overnight low (settles first, ~morning) and the afternoon
high are physically coupled; does the low inform the high beyond its price? (482
paired city-days)

- **Coupling is strong:** corr(high_anom, low_anom) = **+0.83** — a warm/cold
  airmass lifts both, as expected.
- **The market prices the spread almost perfectly:** implied diurnal range vs
  realized — mean(realized − implied) = **+0.04 °F**, corr = **+0.97**. The market
  nails the day's high-minus-low gap.
- **The low adds nothing to the high.** corr(high market_error, low anomaly) =
  **−0.07** (both realized *and* implied) — the high market has already priced
  whatever the low tells you. Even knowing the *realized* morning low (an obs-style
  entry) gives no predictive power on the high's error.
- **Tradeable test:** shifting the high by the low's anomaly (implied_high +
  k·low_anom) collapses win% 64% (k=0) → 29% → 18%, all negative; favorite 65% /
  −3.1¢. The k>0 "less-negative" pnl is the cheap-bucket artifact, not accuracy.

**Verdict:** strong real coupling, but Kalshi is *jointly* calibrated across a
city's high and low markets (range corr 0.97) — nothing to arb. Same theme. Probe:
`--analysis diurnal`.

### #7 — Cross-city W→E lead-lag — mechanism REAL, but FULLY PRICED (no trade)
Weather propagates west→east, so a western city's daily anomaly might lead an
eastern city's. Cities ordered by longitude (LAX→DEN→AUS→CHI→MIA→PHIL→NYC).

- **The physics is visible in the data.** Pooled corr(anomaly), downwind (west
  leads east) vs an upwind placebo (east leads west): lag 0 = +0.39 / +0.39
  (same-day cities just share regional weather — symmetric, as expected), but
  **lag 1 = +0.43 downwind vs +0.30 upwind**, **lag 2 = +0.40 vs +0.23**. The
  downwind-minus-upwind asymmetry (~+0.13–0.17 at lag 1–2) is the genuine
  fingerprint of airmasses moving W→E. This is a *real* detected signal — most
  lead-lag fishing finds nothing.
- **But the eastern market has already priced it.** corr(east market_error[d+lag],
  west anomaly[d]) = **−0.03 @ lag 1**, **−0.01 @ lag 2** (n≈2,800) — dead zero.
  By the time the east is at h12 the airmass is essentially overhead and the
  price reflects it (same public forecasts).
- **Tradeable test confirms no edge.** Shifting the east prediction by the western
  anomaly (pred = implied_east + k·west_anom[d−1]) *destroys* accuracy: win% 63%
  (k=0) → 25% (k=0.5) → 17% (k=1.0), all negative; favorite 65% / −2.8¢. The shift
  moves you off the (already-correct) implied center.

**Verdict:** we detected the propagation physics, but it's public information the
eastern market prices efficiently by h12. Same theme — derivable signal, no gap.
(Caveat: only tested at h12; the airmass is already imminent then. A much earlier
horizon, h36–48, is the only place this could conceivably be unpriced, but the
zero error-correlation even at lag 2 argues against it, and our backfill barely
reaches that far.) Probe: `--analysis leadlag`.

### #6 — Distribution shape / tail mispricing — REAL bias, NOT a standalone trade (→ model feature)
Normalize each event's h12 ladder into an implied PDF over bucket temperatures,
standardize the actual outcome (z = (actual − implied_mean) / implied_sd), and
check the shape. **This is the most actionable result so far.** (940 events)

- **Location is unbiased:** mean z = **−0.02 σ** — no systematic hot/cold lean in
  the market's implied mean.
- **The implied distribution is too WIDE (overdispersed):** SD(z) = **0.78** (< 1).
  Actual outcomes land *closer* to the implied mean than the prices say they
  should. **PIT tail mass lo=5% / hi=3%** (vs ~10%/10% if calibrated) confirms it
  — only ~8% of outcomes fall in the tails the market prices at ~20%. Reality is
  more predictable at h12 than Kalshi prices it; the **shoulders/near-tails are
  too rich and the center is too cheap.**
- **But the mispricing is below the cost floor.** Per implied-σ shell: center
  (0–0.5σ) wins 73% priced 72¢ (1¢ cheap), the 1.0–1.5σ shell wins 16% priced 18¢
  (2¢ rich), the far 1.5σ+ tail wins 2% priced 2.2¢ (fair — agrees with #4). The
  gaps are 1–2¢; after bid/ask + fees **every** YES and NO entry is negative
  (best cells ≈ −1.2¢). No naive buy-center / sell-shoulders book clears the floor.

**Verdict:** a *robust, mechanism-plausible* shape bias (retail spreads bets/lottery
tickets across tails; the market is slow to tighten its distribution as the day
constrains the outcome — same family as the `obs` thermometer-lag edge), but too
small to trade directly. **High value as a calibration feature for the forecast
model: trust the market's implied mean, then SHRINK its variance (~×0.78²).** The
resulting bucket probabilities should be better calibrated than the raw ladder,
especially by de-weighting the over-fat shoulders. This is the first finding that
feeds the edge model rather than just closing a door. Probe: `--analysis distshape`.

### #5 — Favorite momentum / convergence — REFUTED
Does the favorite's price drift predictably, and do bucket price moves trend?

- **Price moves are a random walk.** Autocorr of Δprice[h24→h12] vs Δprice[h12→h6]
  = **−0.03** (n=4798) — no momentum, no intraday reversion.
- **The favorite is fairly-to-richly priced and gets *worse* to buy as the day
  shortens:** buy-favorite-YES-hold-to-settle EV is **−1.8¢ @ h24**, **−3.5¢ @
  h12**, **−6.3¢ @ h6**. Win% rises (50→67→63%) but price rises faster — the
  market firms up *correctly* and the price already reflects it. No "favorite
  underpriced and converging" edge.
- **Chasing risers doesn't beat the favorite:** at h12 the biggest recent riser
  wins 63% (−4.3¢) vs the favorite's 67% (−3.5¢). The biggest faller wins only 6%
  — priced low, so its −2.8¢ isn't an edge either.

Corroborating note: the h24 favorite (−1.8¢) is the *least*-negative entry,
reinforcing the `cwin` finding that **earlier entry on highs beats late entry**
(late favorites pay up). That structural timing edge is already captured by the
city × window map; there is no separate momentum/convergence book to build.
Probe: `--analysis convergence`.

### #4 — Favorite-longshot bias (tail harvesting) — REFUTED
Bin every bucket at h12 by its mid price; sell cheap longshots (buy NO at
100−bid) and buy heavy favorites (YES at ask); grade on the actual winner.

Cheap tails are **well-calibrated** — actual win% sits on top of the implied
price (1–3¢ band wins 0.9%, 3–5¢ wins 4.3%, 5–10¢ wins 5.0%, 10–20¢ wins 13.6%).
After per-leg fees every sell-the-longshot band is negative (−0.3 to −2.4¢).
Heavy-favorite buys are non-monotone (80–90¢ −1.9¢, 90–95¢ +2.6¢, 95–100¢
−3.0¢) — the lone positive cell is noise at n≈90 (binary SE ~±5pts). **No
exploitable favorite-longshot bias from price alone.** Probe: `--analysis longshot`.

### #3 — City × entry-window map — VALIDATED (live as `cwin`)
Different cities firm up at different hours-to-close. A per-city window map beat a
flat window **out-of-sample**: pick the best window per city on the first 60% of
dates, evaluate on the last 40% → map **+2.9¢** vs flat **−2.4¢**. Significance +
month-stability + train/test holdout all checked. Built into the live `weather_cwin`
book (highs): `CHI:18, LAX:18, DEN:18, NYC:10, MIA:24, AUS:24, PHIL:10`.
Probe: `weather_window_sweep.py --validate`.

### #2 — Persistence / seasonal drift — PRICED (no trade)
Temperature is strongly autocorrelated, but the market already prices it. The
market's open-implied mean tracks yesterday's outcome essentially as well as the
actual does (implied corr 0.92 ≥ persistence corr 0.91); the naive "buy
yesterday's-outcome bucket at the open" strategy loses. A faint mean-reversion
residual exists (see below) but is too small to trade on its own.
Probe: `--analysis persistence`.

### #2b — Mean-reversion (fade after extreme) — REAL but TOO SMALL
After an anomalously hot/cold day the market slightly over-extrapolates;
corr(market_error, yesterday's anomaly) ≈ −0.18 pooled. Physically concentrated
in **continental cities** (DEN, PHIL, CHI) as expected. But the best fade
correction only adds **~+0.4¢/trade** — below the fee+noise floor to trade alone.
Kept as a candidate *model feature*, not a standalone book. Probe: `--analysis meanrev`.

### #1 — Ladder overround / sell-the-ladder arb — DEAD
In an N-bucket exclusive market the YES prices "should" sum to ~100¢. Across
every event × snapshot the bids sum < 100 on the tight CLOB; **0% of snapshots**
offered a risk-free sell-the-ladder credit net of per-leg fees. No structural vig
to harvest. Probe: `--analysis overround`.

---

## Earlier findings (pre-backfill, live paper books)

- **Simple TA has no edge.** Phases 3–3.5: over 1,000+ paper trades, `momentum`,
  `reversion`, and `buy_favorite` all lost ≈ −5¢/contract (the round-trip
  spread+fee cost floor). This is what drove the pivot to weather.
- **`buy_favorite` weather baseline** also bleeds the cost floor — confirms the
  ladder is fairly priced; an edge has to come from forecast/timing/cross-market
  information, not from buying the crowd's favorite.
- **NWS forecast & bias-corrected (`cal`) books** collect forecast data and trade
  the forecast bucket; the model-check harness grades the ensemble vs market
  (Brier/log-loss/EV) before any size goes on.
- **Polymarket cross (`pm`)** trades Kalshi toward a divergent Polymarket price
  on the same city (LAX/MIA/AUS, discovered via tags).
- **Obs-confirmed late entry (`obs`)** buys the bucket containing the day's
  running max/min once the real thermometer reading is in past a local cutoff
  hour and the ask is still ≤ cap — the thermometer-lag edge.

---
