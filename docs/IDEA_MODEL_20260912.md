# Idea-model run 2026-09-12 — broad sweep after the August–September close-outs

Broad full-board run, requested by the operator: *"review all the experiments we have done in
this repo and determine three new strategies we could run that we have not done before — a riff
of an existing experiment or something completely new; any prediction market or perpetual
market."* The request names the scope, so the Phase 0.5 menu was skipped. Session role:
Research Lab (read + research-write; no live promotion).

Output: **two promotions** — METALHALT (riff), EARNBEAT (new) — each a pre-registered thesis with a
*built* recon census, **both run the same day after PR #395 merged: METALHALT KILLED at census (premise
falsified), EARNBEAT HOLD (accrual, re-run week of 2026-11-09)**; a **third, PERPMM (riff), was withdrawn the same day** when an independent
operator-approved census (WS-017, `docs/PASSIVE_PERP_CENSUS.md`) closed its premise before this run's
PR merged; and a reconciled holds queue. Nothing here
changes a lifecycle state, a gate, or a verdict; Experiment OS remains canonical.

---

## Phase 0 — grounding (what the record already settled)

**The live book, as the correlation lens.** One real-money line: the `mmsell10` cheap-tail
maker-sell lineage (`Fmmsell10` live canary, `Cmmsell10` parent). Everything else that ever
traded is paper, shelved, retired or killed: weather (all books retired 2026-08-12), theta
(collect-only; theta4's live deployment closed 2026-08-19), PIN15 (retired 2026-07-16),
`freeze1–4` (built 2026-08-13, stood down — no qualifying universe, WS-005 Blocked). Standing
(`xos control-tower`, ops `rl-ct-20260912a`, as of 2026-09-12 12:00 CDT): **no experiment at
IDEA or PROBE** — so nothing below duplicates an open line; five PAPER mmsell experiments; three
mmsell10 LIVE_CANARY lines whose `paper_to_live_canary` gates read PASS (recorded); theta-tail-sell
PAUSED; `freeze-dark-window-pin` RETIRED 2026-09-06 (XOS-000003: 7/7 configured series empty
every cycle, zero paper trades in 24 days) — which is why METALHALT is a **new** experiment on a
new universe, not a reopening. Portfolio: paper realized 30 d $430.22 (paper assumes fills it
would not always get; the north star is real money).

**Closed since the last idea-model run (2026-07-25).** These are the entries a July-era run
would not know:

| line | closed | one-line verdict |
|---|---|---|
| PMDIV (Polymarket-vs-Kalshi weather disagreement) | 2026-08-13 | KILL — the more Polymarket disagrees, the more reliably it is wrong; cross-venue family closed |
| `weather_con` / `weather_concity` | 2026-08-12 | RETIRED — weather has no book left |
| PERP-V1 (3 arms + control, Kalshi crypto perps) | 2026-09-02 | arm A FAIL (24 bps taker round trip vs 8.9 bps spread), B BLOCKED_DATA (no funding source), C NO-GO. **Per-ticker split 2026-09-03: mechanism present on BTC/ETH; a both-legs-passive variant is the only open thread** |
| MARKTANGLE-1 / -2 (conditional reversion / dependence) | 2026-09-03 | streak length carries nothing (wrong sign where powered); crypto threshold persistence real but **unpriceable** (<1% two-sided quotes at T−60m) |
| mmsell10 queue-aware cancel | 2026-09-10 | falsified — "late fills are the good ones"; `Fmmsell10` is not capacity-constrained |

**Base rate (scorecard).** 19 promotions → 1 book that passed (PIN15, later retired) → 0 live
paper books from this pipeline today. Per family: obs-pin / mechanics-blindness 1-for-7 (the only
family with signal); model-vs-quote 0-for-2; lead-lag 0-for-3; favorite-buy 0-for-1; maker 0-for-1
(but `mmsell` predates the pipeline and is the only realizable +EV ever found); structural 0-for-3;
order-flow 0-for-1. Promote conservatively; **testability-NOW and venue age are the binding gates**.

**Meta-lessons this run inherits, restated in one line each.** (1) Price-history edges on
mature markets are dead. (2) Deterministic-about-the-outcome signals survive; homegrown models
die. (3) The pin shape that works is mechanics blindness, not source inattention. (4) Sports
reaction/latency is structurally dead (shared feed). (5) Any real edge accrues to the **maker**;
every taker avenue tested is efficient — and every passive idea carries a mandatory
adverse-selection haircut, measured on prints, never assumed. (6) Edges are cell-concentrated.
(7) Small-n mirages and lookahead bugs are the top process risk (five false-positive classes in
the arb scanner alone; three fill-model mirages in the mmsell family). (8) New since July: HOLD
and BLOCKED_DATA are different verdicts — record BLOCKED_DATA where more evidence can never come.

## Phase 0.5 — scope

Broad sweep, by request. Venue scope note: the operator allowed "any prediction market or
perpetual market". Polymarket (and its September perps) is geofenced for US order placement
and integrated here as signal only; Hyperliquid/Binance have no execution path in this repo and
US-access constraints. **Kalshi's own perps are the one legal, integrated perpetual venue**, so
"perpetual markets" in this run means Kalshi perps (crypto, and the gold/silver perps launched
2026-09-09).

## Phase 1 — board drift since July (web survey; the live-liquidity survey via ops is queued)

The Kalshi public API and kalshi.com are egress-blocked from this sandbox, so the board read is
from press coverage and the repo's own August tape; the ops-channel `kalshi_market_survey` run
that would put numbers on it is queued behind the Control Tower request.

| new since the last run | when | why it matters here |
|---|---|---|
| **15-minute gold & silver markets** (Pyth-settled; ~24 h weekdays, dark weekends) | Aug 2026 | a recurring, high-cadence metals tape — and Pyth XAU/XAG halts daily 17:00–18:00 ET, which the FREEZE probe's "Pyth is continuous" exclusion missed |
| **Gold & silver perpetual futures** (`GOLDPERP`/`SILVERPERP`, Pyth index) | 2026-09-09 | < 2 months old → HOLD by venue age; co-measurable in any perp tape |
| **Public Companies Hub**: KPI markets (Fiscal.ai lines) + earnings-call mention markets | 2026-08-04 | a category the portfolio has never touched, recurring per earnings season; ~5 weeks old |
| **Biotech pilot** (clinical-trial / FDA decision markets, AppliedXL) | 2026-07-16 | lumpy, dozens of settles a year, still a pilot |
| Filed / pending: 24/7 metals schedule, US equities, copper, FX, single-stock perps | Sep 2026 | triggers for holds below, not candidates today |
| Polymarket perps (20× leverage, 67 markets) | 2026-09-03 | not tradeable from the US; symmetric index to Kalshi perps → no lead-lag |

**Live survey (`kalshi_market_survey`, ops `rl-survey-20260912a`, 14 d, open markets only —
settled 15-minute windows are therefore not in it):** 112,704 markets scanned. By category:
Elections $543M / Sports $529M / Economics $131M / Politics $100M / Crypto $80M / **Companies
$14.5M over 533 markets (avg spread 9.9¢)** / **Mentions $2.3M over 857 markets (17.9¢)** /
**Commodities $1.9M over 580 markets (14.3¢)**. So the KPI hub is real and mid-sized, mention
markets are numerous but thin, and the commodities hub's *open* book is small — METALHALT's
capacity clause (P4) is the honest weak axis, and the settled 15-minute tape the census counts
is not visible from an open-market survey. Sports remains the liquidity centre and the mmsell
family's home; crypto ladders remain retail-heavy; economics remains thin and efficient.

## Phase 2 + 3 — slate and screen

Axes: **corr** (shared driver with `Fmmsell10` — negative is good), **edge** (prior given the
meta-lessons), **cost** (net of both-leg fees / spread / adverse selection), **test-now**
(settled data exists today), **cap/age** (capacity and venue age), **infra** (reuse). Scale
−− … ++. Eighteen candidates; mechanics as the outer loop; three anti-anchor slots forced.

| # | candidate (mechanic × market; the fresh signal) | corr | edge | cost | test-now | cap/age | infra | call |
|---|---|---|---|---|---|---|---|---|
| P1 | **PERPMM** — both-legs-passive premium reversion on KXBTC/ETHPERP; signal = perp price vs `reference_price` z-score (maker × perps) | ++ | − | − | + | ++ | ++ | **PROMOTED, then WITHDRAWN same day** — the independent PASSIVE-PERP census (WS-017) measured the premise's *outright price return* under instant maker fills: BTC −4.84 / ETH +0.05 bps after fees, both below a random-direction control → HOLD, no re-run without a mechanically distinct premise. A stricter fill model cannot rescue it. `docs/PERPMM_THESIS.md` kept as the withdrawn pre-registration |
| M2 | MMKPI — mmsell cheap tails on KPI/mention ladders (maker × companies) | −− | + | o | + | o | ++ | **HOLD → mmsell universe review**, not a book: same FLB driver (PORT clusters it); route via `mmsell_universe_review`'s series tiering |
| M3 | UP15-METALS — maker-sell the retail "Up" side of 15-min metals windows (maker × metals) | + | − | −− | + | + | + | **KILL** — a resting offer at a coin flip is MMFLIP (killed 07-11): pure adverse selection with no model gate |
| S1 | **METALHALT** — exchange-closure pin on metals windows inside the Pyth halt (obs-pin × metals; signal = the halt calendar) | ++ | ++ | + | o | − | ++ | **PROMOTE** — FREEZE's mechanism on the settlement-source axis WS-005 asked for; census decides universe size; either outcome closes WS-005 D1 |
| S2 | COMPIN re-run — TWAP endgame pin on commodity averages (obs-pin × commodities) | ++ | + | + | −− | o | ++ | **HOLD (BLOCKED_DATA-ish)** — Pyth Benchmarks history went key-gated 2026-07-31; the probe has no free intraday reference. Trigger: a keyless intraday metals/energy history source |
| S3 | ECON-REACT re-run — post-release quote lag on scheduled prints (obs-pin × economics) | ++ | + | o | o | − | ++ | **HOLD (overdue action)** — scheduled 2026-08-08, never run; one ops request (`econ_react_study`), no build. Do it; it is not a promotion |
| S4 | PIN15-METALS — 60 s-average endgame pin on 15-min metals (obs-pin × metals) | + | o | + | + | + | ++ | **HOLD, folded into S1's census** — PIN15's T-window was falsified live against Pyth-fed makers; metals' lower vol changes the ratio but not the counterparty. The same series scan reports the `outside`-window tape for free |
| S5 | SEASONPIN — cumulative-bound arithmetic on season win-totals (obs-pin × sports) | ++ | + | + | − | + | ++ | **HOLD, trigger FIRES ~2026-10-01** — MLB regular season ends 09-27, the 0-settled-rungs blocker lifts; re-run `kalshi_seasonpin_census` then |
| L1 | GOLDPERP→15M — gold perp mark as the fast leg for 15-min metals (lead-lag × metals) | + | −− | − | − | − | + | **KILL** — both instruments track the same Pyth index (XGAME symmetry); PERP-V1 arm C's lead test was null at the tape's cadence |
| L2 | PM-PERP-DIV — Polymarket perp vs Kalshi perp premium divergence (RV × perps) | ++ | −− | − | − | −− | o | **KILL** — same index both sides; PM order placement is geofenced; PMDIV's lesson (when PM disagrees, PM is wrong) |
| L3 | OPTRV — hub thresholds vs CME options-implied density (RV × commodities) | ++ | o | − | − | o | − | **HOLD (unchanged)** — fillability trigger fired 07-12, but the "model" needs paid CME options data; trigger: a free options-implied source. `kalshi_deribit` already covers the crypto analogue (efficient) |
| N1 | **EARNBEAT** — consensus-anchoring bias on KPI ladders; signal = public beat base rate vs the consensus rung's price (directional × companies) | ++ | + | o | −− | − | + | **PROMOTE as HOLD-by-venue-age with a built census** — the anti-anchor slot: new category, external deterministic base rate, no homegrown model. Census names the accrual trigger |
| N2 | BIOBASE — FDA approval base rate vs biotech pilot markets (directional × biotech) | ++ | + | o | −− | −− | − | **KILL** — dozens of one-off settles a year on a pilot; no track record possible (TRACKPIN shape) |
| N3 | MENTION-corpus — transcript base-rate model for mention markets (model × companies) | ++ | − | o | −− | o | −− | **HOLD (unchanged)** — still no cheap timestamped transcript source; the hub's livestream links don't change that |
| T1 | MENTIONLOCK — mid-call word-said one-way lock (structural × companies) | ++ | + | o | −− | − | − | **HOLD, folded into N3** — the lock is real; ground truth still needs per-call timestamps; watched-feed adjacency (SPORTLOCK) |
| T2 | METALS-EQUITY-OPEN — 15-min metals windows around the 18:00 ET resume: first post-resume print vs the halted quote (event-cond × metals) | + | o | − | o | − | ++ | **KILL as a standalone; kept as METALHALT's `boundary` class** — it is the wrong-pin risk (P3), not an edge |
| E1 | EQUITY-HUB / single-stock perps (any × equities) | ++ | ? | ? | −− | −− | o | **HOLD (pre-listing)** — CFTC filings only; trigger: first settled month |
| X1 | XPERP-FUND — funding-rate arbitrage across Hyperliquid/Binance/Kalshi (RV × perps) | ++ | o | − | −− | −− | −− | **KILL for this repo** — no execution path, US access constraints, Kalshi funding unreadable (PERP-V1 arm B); scope, not edge |

**Why these, and why only two survive.** The record's only passing family is the mechanics pin
and its only realizable +EV is a maker book. METALHALT is the pin on a universe the record itself
asked for (WS-005 D1). EARNBEAT is the forced anti-anchor: a category with zero portfolio
exposure and an external base rate rather than a model, held honestly behind the venue-age gate.
PERPMM was the maker slot and the one thread PERP-V1's close-out named as open — and the same day
this run promoted it, an independent census (WS-017) measured that the premise's outright price
return does not survive even optimistic fills, which is the honest end of that thread: the
record's own rule is no revival without a mechanically distinct premise, and none is offered
here. Both survivors are uncorrelated with `Fmmsell10` and with each other; PORT's "second
independent +EV stream" is the binding constraint, and each is a candidate for it. **No third
candidate on the slate clears the screen today** — the best advanceable items are holds with
dated triggers (SEASONPIN ~2026-10-01, the overdue ECON-REACT re-run), listed below.

## Phase 4 — promotions (pre-registered theses + built censuses)

| idea | thesis | census script (allowlisted) | census verdict rule | what a kill closes |
|---|---|---|---|---|
| ~~PERPMM~~ | `docs/PERPMM_THESIS.md` (withdrawn) | none shipped | — superseded by `docs/PASSIVE_PERP_CENSUS.md` (HOLD, 2026-09-12) | already closed: passive premium-fade price return ≤ control |
| **METALHALT** | `docs/METALHALT_THESIS.md` | `scripts/kalshi_metalhalt_census.py` | PROMOTE-TO-PROBE iff ≥ 40 settled `inside`/`tail` windows with volume AND post-halt trading at ≥ 3¢ observed; HOLD (universe absent) if Kalshi lists no halt windows — **ran twice 2026-09-12: KILL (PREMISE).** 62 calendar-pinned windows exist, but their settlement values move inside the halt (C0, 2/2 stretches): Kalshi settles metals on Pyth's 24/7 indices, so nothing is decided early | the FREEZE family on Kalshi → WS-005 D1 answered on both axes; recommend ABANDONED |
| **EARNBEAT** | `docs/EARNBEAT_THESIS.md` | `scripts/kalshi_kpi_census.py` | TESTABLE-NOW iff ≥ 100 settled KPI threshold markets with volume AND ≥ 60 readable pre-report quotes; else HOLD, re-run week of 2026-11-09 — **ran 2026-09-12 (`kpi-census-1`): HOLD (ACCRUAL), 43 / 36 against floors 100 / 60; 396 open** | the consensus-anchoring premise |

Ops requests (run from default-branch code after merge; one at a time on the shared channel):

```
{"type":"script","name":"kalshi_metalhalt_census","args":["--max-event-pages","80"],"id":"metalhalt-census-1"}
{"type":"script","name":"kalshi_kpi_census","args":["--max-event-pages","80"],"id":"kpi-census-1"}
```

A census verdict is printed, not recorded. Only a PASS on the full probe's pre-registered
predictions creates an Experiment OS experiment (Research Lab standard workflow); the census
decides whether the probe is worth writing.

## Holds queue — reconciled (trigger state as of 2026-09-12)

| hold | trigger | state |
|---|---|---|
| **PERP passive variant** | a fill model in a pre-registration | **CLOSED 2026-09-12** — PASSIVE-PERP census (WS-017) HOLD on the price screen; PERPMM withdrawn. Revisit only with a mechanically distinct premise |
| **FREEZE** (universe question, WS-005 D1) | a qualifying universe on the settlement-source axis | **CLOSED 2026-09-12** — METALHALT census: the metals source is continuous (KILL, PREMISE); grain/soft axis had no universe (RETIRED 09-06). Recommend WS-005 → ABANDONED |
| **ECON-REACT re-run** | more genuine econ prints settled | **FIRED, overdue** — run `econ_react_study` v2 via ops; no build |
| **SEASONPIN** (MLB primary; WNBA borderline) | MLB rungs settle | **FIRES ~2026-10-01** — re-run the census then |
| **OPTRV** | free options-implied source for CME commodities | parked (fillability fired 07-12; plumbing is the blocker) |
| **COMPIN** | keyless intraday reference history (Pyth key-gated since 07-31) | parked — BLOCKED_DATA-shaped; do not re-run as-is |
| **MMX family** (FIELD/CULTURE/DOOM/MENTION/MMCOM/MMART/PGAMM/ELECMM) + **MMKPI** (this run) | — | **RETIRED as holds** — they are mmsell *cells*; the owning path is `mmsell_universe_review` series tiering, not the idea model |
| **PIN60 / ALT15 / CROSSFREQ** | pin15 gate | **RETIRED** — parent retired 07-16; the trigger can never fire as written |
| **CRYPSUB / NEST** | theta4 n≥80 | **RETIRED** — theta4's live deployment closed 08-19; no parent |
| **WXRAIN / MOSUM** | a new mechanic slot on weather | **RETIRED** — weather has no book; the family is closed |
| **STREAMPIN / STREAMRANK** | intra-window tape appears | parked (census HOLD, unchanged) |
| **ART — GUARPIN** (ARTSUM instrument-less) | Oct/Nov evening sales settle | parked; check after the November sales |
| **RTPIN / BOXPIN** | a cheap public-history angle | parked |
| **MENTION-corpus** (absorbs MENTIONLOCK, T1) | cheap timestamped transcripts | parked (anti-anchor reserve) |
| **RATELAG** | a live macro shock to the front Fed contract | parked |
| **HURR** | first landfall-threat storm | parked (below-normal season) |
| **XLOCK-P1** | a rules-text matcher that finds ≥ 200 pairs | parked |
| **PIN15-METALS** (S4, this run) | METALHALT census `outside`-window read | **RETIRED** — the metals settlement feed is continuous and the 15-min tape is a live market to the last minute; PIN15's own T-window kill transfers |
| **EQUITY-HUB / single-stock perps** (E1, this run) | first settled month after listing | parked |

## What this run does NOT do

- It does not register anything in Experiment OS, change any book, or touch `MMSELL_VARIANTS`.
- It does not run the two censuses: they run default-branch code via the ops channel after this
  PR merges, and the merge itself is an operator decision because the diff touches
  `scripts/ops_runner.py`'s allowlist (a hard-stop item under `DEC-012`).
- It does not read a standing into any of the above; the Control Tower read was queued and is
  reported separately when it lands.
- It does not revive PIN15, FEDRV, OFLOW, XGAME, PMDIV, MARKTANGLE or the taker perp arm; each
  candidate that touches a dead family names its material difference in its thesis.
