# Idea-model scorecard — the promote→verdict ledger

The **quantitative** companion to `RESEARCH_JOURNAL.md`'s qualitative meta-lessons. The
`kalshi-idea-model` skill reads this in **Phase 0** (to calibrate how skeptical to be, and to spot
families that are 0-for-everything) and updates it in **Phase 4** (append each promotion; set the
verdict when it lands). Newest promotions at the top of the ledger.

**Maintain it:** the moment the skill promotes an idea, add a row with status `pending probe`.
When a probe/paper verdict lands (logged in `RESEARCH_JOURNAL.md`), update that row's **verdict**
and **outcome**. Keep the base-rate and per-family tallies below in sync.

---

## Base rate (as of 2026-07-12, run 2)

**11 idea-model promotions → 1 currently-live paper book.** The pipeline is working *as a filter*
(most promotions are cheap ruling-outs, no paper bled), but the promote→book conversion is low, so
**promote conservatively and lean on the testability-NOW + venue-age gates.**

| outcome | n | which |
|---|---|---|
| **live paper book (gates passing)** | 1 | PIN15 |
| became a book, later shelved | 1 | THETA (distribution-shape model error) |
| **killed at probe** (clean ruling-out) | 6 | XGAME, TFAV, WCPROP, MLBWX, PINNED, DECAY |
| **UNTESTABLE — venue not ready** (data absence, not a kill) | 2 | FREEZE, COMPIN |
| **pending probe** | 1 | SEASONPIN (census-staged) |

Plus one run (2026-07-10 run2) that promoted **0 of 28** candidates — a legitimate, good outcome.

## Per-family hit-rate (the load-bearing calibration)

| edge family | promotions | passed | read |
|---|---|---|---|
| **observation-pin / mechanics-blindness** (compute fair value the quote lags; deterministic) | PIN15, PINNED, FREEZE, COMPIN, SEASONPIN | **1** (PIN15) | The **only family that has ever passed.** But 2 of 5 came back UNTESTABLE (new-venue data absence) and 1 killed — so "obs-pin" is necessary, not sufficient; the testability-NOW gate is what separates PIN15 from FREEZE/COMPIN. SEASONPIN (pending) is the family's testability-NOW-first promotion: census on an existing settled tape before any probe code. |
| **model-vs-quote** (homegrown forecast/vol model beats the market) | THETA, MLBWX | **0** | 0-for-2. Distribution-shape/model error each time. **Do not re-promote without a genuinely new model** under fresh pre-registration. |
| **lead-lag / cross-venue** (one venue leads, trade the follower) | XGAME, WCPROP | **0** | 0-for-2. XGAME's symmetry finding (both venues track the shared feed) is structural and transfers across sports. |
| **favorite-buy** (back the underpriced favorite) | TFAV | **0** | 0-for-1 (−3.6¢). The FLB favorite side accrues to makers, not takers. |
| **structural / deadline premium** | DECAY | **0** | 0-for-1. By-date "hope" was *under*priced, not over. |

**The one-line lesson the scorecard adds to the meta-lessons:** obs-pin/mechanics-blindness is the
only family with any signal, and even it fails when the settled data doesn't exist yet — so the
highest-leverage screen is **testability-NOW**, not edge cleverness.

---

## Ledger

| date | idea | family | scope source | verdict date | verdict | outcome |
|---|---|---|---|---|---|---|
| 2026-07-12 | **SEASONPIN** | obs-pin (cumulative-bound arithmetic) | **scoped (mechanics hunt)** | — | **pending probe** | Census-staged (`docs/SEASONPIN_THESIS.md`): win-total rungs decided by standings math; census must clear early-expiration + n-floor before any probe code. |
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
