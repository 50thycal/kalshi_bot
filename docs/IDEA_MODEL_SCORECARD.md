# Idea-model scorecard — the promote→verdict ledger

The **quantitative** companion to `RESEARCH_JOURNAL.md`'s qualitative meta-lessons. The
`kalshi-idea-model` skill reads this in **Phase 0** (to calibrate how skeptical to be, and to spot
families that are 0-for-everything) and updates it in **Phase 4** (append each promotion; set the
verdict when it lands). Newest promotions at the top of the ledger.

**Maintain it:** the moment the skill promotes an idea, add a row with status `pending probe`.
When a probe/paper verdict lands (logged in `RESEARCH_JOURNAL.md`), update that row's **verdict**
and **outcome**. Keep the base-rate and per-family tallies below in sync.

---

## Base rate (as of 2026-07-21, Area-1 scoped run)

**14 idea-model promotions → 0 currently-live paper books** (PIN15, the one success, RETIRED
2026-07-16 when its target T-window was falsified live). The pipeline keeps working *as a filter*
(most promotions are cheap ruling-outs, no paper bled), but the promote→book conversion is very
low, so **promote conservatively and lean on the testability-NOW + venue-age gates.** The
2026-07-21 Area-1 (untouched-universe) run added 3 promotions, all pending a first ops run.

| outcome | n | which |
|---|---|---|
| **live paper book (gates passing)** | 1 | PIN15 (later RETIRED 2026-07-16 — T-window falsified) |
| became a book, later shelved | 1 | THETA (distribution-shape model error) |
| **killed at probe** (clean ruling-out) | 6 | XGAME, TFAV, WCPROP, MLBWX, PINNED, DECAY |
| **UNTESTABLE — venue not ready** (data absence, not a kill) | 2 | FREEZE, COMPIN |
| **census-stage HOLD/borderline** (not yet a full-probe go/no-go) | 2 | SEASONPIN (MLB HOLD, WNBA borderline), STREAMPIN (pending census) |
| **pending probe** (promoted 2026-07-21 Area-1, not yet run) | 2 | ECON-REACT, FEDRV |

Plus one run (2026-07-10 run2) that promoted **0 of 28** candidates — a legitimate, good outcome.

## Per-family hit-rate (the load-bearing calibration)

| edge family | promotions | passed | read |
|---|---|---|---|
| **observation-pin / mechanics-blindness** (compute fair value the quote lags; deterministic) | PIN15, PINNED, FREEZE, COMPIN, SEASONPIN, ECON-REACT, STREAMPIN | **1** (PIN15) | The **only family that has ever passed.** But 2 of 5 came back UNTESTABLE (new-venue data absence) and 1 killed — so "obs-pin" is necessary, not sufficient; the testability-NOW gate is what separates PIN15 from FREEZE/COMPIN. SEASONPIN was the family's testability-NOW-first promotion. The 2026-07-21 Area-1 run added two cousins: **ECON-REACT** (obs-pin on a *scheduled release* — the number is public but the quote lags; the one econ angle the edge map sanctions, and the honest test of whether the "doesn't-generalize-off-weather" lesson has an exception for *latency* vs *inattention*) and **STREAMPIN** (obs-pin on lagged stream counts, census-first). |
| **model-vs-quote** (homegrown forecast/vol model beats the market) | THETA, MLBWX | **0** | 0-for-2. Distribution-shape/model error each time. **Do not re-promote without a genuinely new model** under fresh pre-registration. |
| **lead-lag / cross-venue** (one venue leads, trade the follower) | XGAME, WCPROP | **0** | 0-for-2. XGAME's symmetry finding (both venues track the shared feed) is structural and transfers across sports. |
| **favorite-buy** (back the underpriced favorite) | TFAV | **0** | 0-for-1 (−3.6¢). The FLB favorite side accrues to makers, not takers. |
| **structural / deadline premium / internal-coherence** | DECAY, FEDRV | **0** | 0-for-1 so far (DECAY: by-date "hope" was *under*priced, not over). **FEDRV** (pending) is the internal-coherence variant — convolved per-meeting `KXFEDDECISION` vs the direct `KXRATECUTCOUNT` ladder; prior LOW (the 0.3¢ count spread signals it is already arbitraged), promoted as a cheap, uncorrelated ruling-out of the entire rates category. |

**The one-line lesson the scorecard adds to the meta-lessons:** obs-pin/mechanics-blindness is the
only family with any signal, and even it fails when the settled data doesn't exist yet — so the
highest-leverage screen is **testability-NOW**, not edge cleverness.

---

## Ledger

| date | idea | family | scope source | verdict date | verdict | outcome |
|---|---|---|---|---|---|---|
| 2026-07-21 | **ECON-REACT** | obs-pin (scheduled-release reaction) | **scoped (Area 1: untouched universes)** | — | **pending probe** | Post-econ-print reaction-lag pin (`docs/ECON_REACT_THESIS.md`, `scripts/econ_react_study.py`). Promotes the parked PINNED post-jobs-print +43.4¢/ct residual with its own pre-registration + a release-time audit. Kalshi-only probe; awaiting first ops run. |
| 2026-07-21 | **FEDRV** | structural (internal coherence / RV) | scoped (Area 1) | — | **pending probe** | Kalshi Fed-path coherence: convolved per-meeting `KXFEDDECISION` vs the direct `KXRATECUTCOUNT` ladder (`docs/FEDRV_THESIS.md`, `scripts/fed_rv_study.py`). Prior LOW (0.3¢ count spread ⇒ likely efficient); promoted as a cheap, uncorrelated ruling-out. |
| 2026-07-21 | **STREAMPIN** | obs-pin (streaming-count) | scoped (Area 1) | — | **pending census** | Port of the `con`/`obs` thermometer to `KXARTISTSTREAMS` music-streaming markets (`docs/STREAMPIN_CENSUS.md`, `scripts/kalshi_stream_survey.py`). Census-first (C1 cumulative instrument / C2 intra-window tape) before a thesis; the C3 external stream-proxy feasibility test is the thesis-stage gate. |
| 2026-07-12 | **SEASONPIN** | obs-pin (cumulative-bound arithmetic) | **scoped (mechanics hunt)** | 2026-07-12 | **CENSUS: HOLD (MLB) / BORDERLINE (WNBA)** | Census (`docs/kalshi_seasonpin_census.py`, 2 runs — 1st had a classification bug, fixed in PR #43): MLB (named target) 0 settled rungs → HOLD, too early. WNBA (extension, surfaced by discovery) 40 settled — exactly at the n-floor, candle-coverage + volume unconfirmed → not yet a clean promote to full probe. Full detail: `RESEARCH_JOURNAL.md` "SEASONPIN CENSUS 2026-07-12". |
| 2026-07-12 | **COMPIN** | obs-pin (TWAP mechanics) | scoped (commodities hub) | 2026-07-12 | **UNTESTABLE** | 0 settled TWAP markets yet; 35 settle before Jul 31 → re-run ~Jul 14-16. Not a kill. |
| 2026-07-11 | **FREEZE** | obs-pin (exchange-closure) | broad + new-venue | 2026-07-11 | **UNTESTABLE** | Source-frozen ag/soft markets barely exist yet. Not a kill; trigger = universe grows to hundreds. |
| 2026-07-11 | **PIN15** | obs-pin (60s-average pin) | **scoped (15-min crypto)** | 2026-07-11 | **PASS** (P1/P2/P4) | **Live paper book.** The one success — and it came from a scoped dive. |
| 2026-07-10 | **DECAY** | structural (deadline premium) | broad | 2026-07-10 | **KILL** | −19.97¢/ct; by-date hope is under-priced. Family closed. |
| 2026-07-10 | **PINNED** | obs-pin (source-inattention) | broad | 2026-07-10 | **KILL** (P2 fail) | +1.8¢ < bar; converges once source is public. Off-weather source-inattention doesn't generalize. |
| 2026-07-09 | **MLBWX** | model-vs-quote (rain→runs) | broad | 2026-07-09 | **KILL** (P2 −1.5¢) | MLB totals price weather efficiently. |
| 2026-07-04 | **XGAME** | lead-lag (in-play PM→Kalshi) | broad | 2026-07-09 | **KILL** (P2/P3) | Symmetric shared feed — no follower to pick off. Family ruled out across sports. |
| 2026-07-04 | **TFAV** | favorite-buy (crypto) | broad | 2026-07-09 | **KILL** (−3.6¢ @ n=210) | Favorite side of the FLB accrues to makers. |
| 2026-07-04 | **WCPROP** | lead-lag (WC winner ladder) | broad | 2026-07-09 | **KILL** (0 lag) | Post-match repricing completes within one cycle. |
| 2026-07-03 | **THETA** | model-vs-quote (crypto tail-sell) | Claude-originated | 2026-07-09 | **SHELVED** | Built as a paper book; SpotModel undersamples tails (distribution-*shape* error, 1.4-2.6× realized). Collect-only. |

_Provenance: this scorecard was created 2026-07-12, backfilled from `RESEARCH_JOURNAL.md` +
the `IDEA_MODEL_*` run docs, as the base-rate feedback loop recommendation from the six-run review._
