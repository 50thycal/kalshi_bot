# Idea-Model Run #2 — 2026-07-12 (scoped: settlement-mechanics hunt)

Scoped run (Phase 0.5 menu; user picked **"Mechanics hunt"** over OPTRV / MMX pre-stage / broad
sweep): a board-wide deep dive on the **one validated family** — settlement-arithmetic gaps
(averages, windows, rounding, cumulative bounds the flashing quote doesn't internalize) —
*outside* the already-probed 15-min crypto and commodity-hub cells. Grounded in the scorecard
(10 promotions → 1 live book; obs-pin the only family with a pass), the run-#1 doc from earlier
today (`IDEA_MODEL_20260712.md`, which promoted COMPIN → UNTESTABLE-now), and fresh data: a
`kalshi_market_survey` ops run (`idea-mech-survey-0712`, 68,727 open markets scanned) plus web
verification of the load-bearing settle rules.

**Run outcome: 1 promote (SEASONPIN), 1 new hold (STREAMPIN, venue-integrity trigger), 3 folds
into existing holds, 1 hold reaffirmed (PIN60), 6 kills.** Scorecard updated (11 promotions).

---

## Phase 0/1 — grounding + scoped board facts

**Correlation baseline** (loop run #36, 2026-07-11): mmsell3 **97/150 @ +3.5¢** (gate ~days
away), pin15 **21/150 @ −16.8¢** (early, negative), theta4 2/80, weather_con/concity. Graveyard
and meta-lessons as in run #1, plus two anti-regeneration facts pulled for this scope:

- **PINNED v4's core cell already tested the gas-average aggregation pin** — weekly AAA markets
  post-pin = **+0.02¢ (perfectly efficient)**. Aggregation pins on AAA gas are DEAD, not open.
- **CLINCHMATH / TOUCH-LOCK / VOTEPIN / SETTLE-TWAP** (07-10 run2) killed the event-recompute,
  touched-barrier, watched-tally, and near-strike-TWAP forms. Any new candidate here must be a
  *slow-accretion* pin with a nameable difference from those shapes.

**Survey facts that decided screens** (`idea-mech-survey-0712`, 14-day volume):

- **Entertainment is NOT a backwater: 48.1M contracts / 6,272 markets / 302 series** (vs weather
  2.7M, commodities 1.3M). Real capacity exists off-sports.
- **KXRATECUTCOUNT** (cumulative rate-cuts ladder) is liquid but **0.4¢ average spread** —
  measured efficient; the count-arithmetic there is priced.
- **Transportation: 1 series / 1 market** — TSA-style weekly-throughput aggregates are extinct
  on today's board.
- Sports 1.70B vol is WC-inflated (final Jul 19); Mentions small (3.5M / 624 mkts, 14.8¢ spreads).

**Web verification (settle rules):**

- **KXARTISTSTREAMS = weekly per-artist Spotify stream ladders** ("How many streams will artist
  have this week"), settling on Spotify's published weekly charts; single markets have drawn
  **$3M+ volume**. But the family is mid-scandal: June 2026 bot-stream manipulation settled a
  market on later-revised data; **Spotify slashed 500k streams retroactively and is pressuring
  Kalshi/Polymarket to de-brand** (Bloomberg/FT/Music Ally, early Jul 2026). Two structural
  consequences: settlement values are *revisable downward* (breaks the monotone lock), and the
  family carries live delisting risk. Also: official *daily* per-artist stream counts are not
  published (daily charts are rank-only at artist level; song-level dailies do publish counts) —
  artist-market partials are approximations, song-market partials are exact.
- **Season win-total ladders EXIST on Kalshi** (Pro Baseball win-totals category: per-team
  benchmark ladders, e.g. ARI ≥75 @ 71¢ / ≥80 @ 55¢, all 30 teams; NFL versions from September).
  Cumulative-count arithmetic with **irrevocable public partials** (a won game cannot be
  un-won) — the integrity property the streams family just demonstrably lost.

## Phase 2–3 — slate + screen (12 in-scope candidates)

Scoped depth: every candidate is the mechanics-pin shape applied to a different settle-rule cell.
Scored −− … ++ on the six axes; **Test** includes testability-NOW, **Cap** includes venue
age/integrity.

| # | Candidate (settle-rule cell) | Corr | Edge | Cost | Test | Cap | Reuse | Call |
|---|---|---|---|---|---|---|---|---|
| **S1** | **SEASONPIN — cumulative win-total rungs decided by standings arithmetic (MLB→NFL)** | o | + | + | **++** | o | + | **PROMOTE** — slow-accretion decidedness (not CLINCHMATH's event recompute); settled 2025+2026 tape + free game logs = gradeable TODAY; census must clear early-expiration + window questions before any full probe |
| S2 | STREAMPIN — weekly Spotify stream ladders locked by public daily partials | ++ | + | + | + | −− | + | **HOLD (new)** — mechanism real and capacity proven ($3M/market), but the live manipulation/delisting dispute breaks the lock (retroactive revisions) and threatens the venue; trigger below |
| S3 | STREAMRANK — weekly Top-Artist rank markets from daily rank partials | ++ | o | o | o | −− | + | **FOLD → STREAMPIN hold** — same venue risk, weaker (ordinal, approximate) partials |
| S4 | PIN60 — hourly crypto 60s-average endgame pin (pin15's mechanic, hourly cadence) | −− | + | + | + | + | ++ | **HOLD reaffirm** — behind pin15's n≥150 gate; parent currently −16.8¢ @ n=21 — expanding a family whose parent is failing early is doubling down, not diversifying |
| S5 | TRACKPIN — public-tracker threshold markets (layoffs.fyi counter, Hormuz AIS) | ++ | o | o | − | −− | o | **KILL** — monotone public partials are real but every instrument is a one-off annual/one-time settle (KXLAYOFFSYINFO = 1 market @ 91¢); can't grind monthly P&L; revisit only if recurring tracker series appear |
| S6 | MENTIONLOCK — mid-event word-said one-way lock on mention markets | ++ | − | o | −− | − | − | **FOLD → MENTION-corpus hold** — lock is real but ground truth needs bespoke per-event transcript timestamps; thin capacity (3.5M/14d); watched-feed adjacency (SPORTLOCK kill) |
| S7 | MOSUM — monthly climate aggregates (month-avg temp) pinned by elapsed days | − | + | o | + | − | ++ | **FOLD → WXRAIN hold** — same monotone-accumulation shape already held; weather-correlated (heavy penalty) + monthly cadence too thin to change the call |
| S8 | GASPIN — AAA weekly/monthly average pinned by elapsed daily prints | + | −− | o | + | − | ++ | **KILL — regeneration.** PINNED v4 core cell measured exactly this: +0.02¢ post-pin. The graveyard answered it |
| S9 | CUTCOUNT — Fed cumulative-cuts year ladder decided meeting-by-meeting | + | −− | − | + | o | + | **KILL** — measured efficient today (0.4¢ spread on KXRATECUTCOUNT); the most-watched arithmetic in finance (EIALAG/VOTEPIN adjacency) |
| S10 | ELECTALLY — election-night seats-ladder rungs locked as races are called | + | −− | o | − | − | o | **KILL** — VOTEPIN parent: intensely-watched tally feed → latency race, not an inattention discount; plus lumpy one-night cadence |
| S11 | VIEWLOCK — view-counter touch/threshold markets (counts public + monotone) | ++ | −− | o | − | −− | o | **KILL** — TOUCH-LOCK parent (post-touch converges fast); pre-touch is velocity extrapolation = the dying model-vs-quote half |
| S12 | TSAPIN — weekly TSA throughput ladder from daily public prints | ++ | + | + | − | −− | + | **KILL** — the shape is textbook (exact public daily partials) but the instrument is extinct: Transportation = 1 open market on today's survey |

### Why SEASONPIN clears where its graveyard neighbors died

1. **vs CLINCHMATH/WCPROP** (derived ladders reprice within one cycle): those die because a
   decisive, watched event triggers everyone's recompute at once. Win-total rungs mostly go
   dead **mid-way through an unrelated game** — no trigger, no headline, no cycle to reprice
   within. It's COMPIN's continuous-accrual argument, on a venue whose tape already exists.
2. **vs TOUCH-LOCK/PINNED** (post-public convergence is fast; post-pin ≈ favorite drift): the
   thesis pre-registers PINNED's own killer control (P2: must beat undecided 85–95¢
   favorite-buys) and leans on the **elimination side** (not a touch); P3 kills it if only the
   clinch side works. Early expiration — the rules detail that would zero the window — is a
   pre-registered census kill (median decided→settled gap <24h → KILL, no full probe).
3. **vs the capacity kills** (BOXPIN/NFLXPIN one-off settles): ~450 MLB rungs decide on a rolling
   basis Jul–Sep, then NFL adds ~500 from September — a stream, not an event. Honest floor
   stated in the thesis: possibly a $20–60/mo book; acceptable as uncorrelated ballast.

**Honest weaknesses, stated now:** capacity per rung is unmeasured (not a top-50 series; the
census counts real volume/spreads); the whole edge could be an early-expiration artifact (killed
cheaply at census); sports-category overlap with mmsell needs the double-count check the thesis
specifies; and capital lockup makes small discounts not worth taking (≥3¢ bar pre-registered).
Full pre-registered thesis: `docs/SEASONPIN_THESIS.md`.

### Why STREAMPIN holds instead of promoting

Testability-NOW is actually satisfied (months of settled weekly markets + archived public chart
history) — on that gate alone it would promote. It holds because the **venue-integrity gate**
(the venue-age gate's spirit) is failing *this week*: settlement values were just shown to be
retroactively revisable (a monotone-lock thesis cannot survive revisable partials), Kalshi's
rules response is unknown, and Spotify is actively pressuring the listings. Probing a family
that may be delisted or re-ruled mid-thesis is FREEZE-shaped waste. **Trigger (re-screen, cheap
web check ~2026-07-26):** the dispute resolves with weekly stream markets still listed AND rules
text confirming settlement uses first-published chart values. If both hold, the pre-staged census
is: enumerate settled KXARTISTSTREAMS (count/volume/spread; song-level vs artist-level split),
then measure Thursday-entry (6/7 days of partials public) locked-side discounts vs outcomes.

## Updated holds queue (reconciled; trigger-state per hold)

| hold | trigger | state |
|---|---|---|
| **OPTRV** (hub RV vs CME options-implied) | fillability census | **FIRED 07-12** (depth read fixed: gold ~2025 / oil ~1169 / coffee ~1280 / nickel ~1474 within 2¢) — assessable now; remaining work = the RV edge design + CME density plumbing. Prime candidate for the next scoped dive |
| COMPIN (TWAP-window pin) | first average contracts settle | **fires ~2026-07-14/16** (35 settle before the Jul-31 Pyth deadline; nearest Jul 13) → re-run `kalshi_compin_study` |
| MMX family (FIELD/CULTURE/DOOM/MENTION/MMCOM/MMART) | mmsell3 n≥150 fill-realism gate | ~days away (97/150 @ +3.5¢, ~12 settles/8h); FLB calibration confirmed off-sports 07-12 |
| **STREAMPIN (+STREAMRANK)** — NEW this run | Spotify/Kalshi dispute resolves w/ family listed + first-published-values rule confirmed | check ~2026-07-26; census pre-staged in run doc |
| PIN60 + ALT15 (pin15 variant ladder) | pin15 n≥150 gate | parked; parent negative at n=21 — do not jump |
| ART family (GUARPIN; ARTSUM instrument-less) | Oct/Nov evening sales + a sale-total listing | parked (pre-season) |
| CRYPSUB / NEST | theta4 n≥80 gate | parked (n=2, very slow) |
| RTPIN/BOXPIN (entertainment obs-pin) | a cheap collector or public-history probe angle | parked; NOT superseded by STREAMPIN (different sources), but re-examine together if STREAMPIN's trigger fires |
| MENTION-corpus (absorbs MENTIONLOCK) | cheap transcript-timestamp source appears | parked (anti-anchor slot) |
| WXRAIN (absorbs MOSUM) | new mechanic slot on weather opens | parked (portfolio-saturated category) |
| RATELAG (KXFED back-rung lag) | a live macro shock to the front Fed contract | parked (CUTCOUNT kill does not touch it — different mechanic) |
| CROSSFREQ | after pin15 reads out | parked |
| HURR | first landfall-threat storm | parked (below-normal season) |
| FREEZE | grain/soft settled universe reaches hundreds | parked (8 as of 07-12; ride-along recheck inside COMPIN re-runs) |

## Handoff

**One promotion: SEASONPIN** (`docs/SEASONPIN_THESIS.md`), staged census-first:

1. **Recon census (cheap, run via ops):** discover win-total series tickers → settled/open rung
   counts, per-rung volume + spreads, rules text for early-expiration, decided-vs-settled gap on
   ~20 sampled 2025/2026 rungs. Kill/hold criteria pre-registered in the thesis (n<40 → HOLD;
   median window <24h → KILL). No full probe is written unless this clears.
2. **Full probe only if census clears:** `scripts/kalshi_seasonpin_study.py` (read-only; Kalshi
   public REST candles + free MLB Stats API game logs; allowlist in `ops_runner.py`), grading
   P1–P4. Verdict lands in `RESEARCH_JOURNAL.md` + the scorecard row updates.

No other probe work this run. The nearest-term portfolio actions remain the queued triggers:
COMPIN's re-run (~Jul 14–16), mmsell3's gate (~days) unblocking MMX, and OPTRV's fired trigger
awaiting its own scoped design dive.

*Scorecard updated: SEASONPIN appended as promotion #11, pending probe
(`docs/IDEA_MODEL_SCORECARD.md`).*
