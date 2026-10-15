# Does entry timing relative to the real game predict mmsell P&L?

Research Lab, 2026-10-15. Paper and read-only. **This document authorizes nothing.** Experiment
OS is canonical for experiment state. Any live consequence is a Live Ops hard stop.

Sections 1–5 are the **pre-registration**, with Amendment A (§2A). All of it was committed before any P&L was read by entry
time relative to the event. The live P&L by hours before **close** was already known
(docs/MMSELL_LIVE_CANARY_PATTERNS.md §1); that is the hypothesis source, not evidence for it.

## 1. Why

- For sports, `close_time` is a fallback, not the game time. On the API it is start + 48h or
  + 72h, and + 14 days on tennis. On a settled `can_close_early` market it is rewritten to the
  moment the outcome became certain.
- So "hours to close" cannot say whether an entry was placed before the game, during it, or
  near its end. Check 3 of the canary-patterns doc could not be run for that reason.
- **Suspected mechanism:** informed flow picks off resting orders. If that happens mostly
  in-play, or just before the start, timing relative to the start will show it.
  - Sports totals: +1.9¢ on paper, −2.4¢ live.
  - Crypto: +0.3¢ on paper, −8.3¢ live.

## 2. Sources for the event start (STEP 1)

Checked cheapest first. The field inventory was read from the live API through the ops runner
(ops `evtime-probe-20261015a`); the values are checked by the `--fields` and `--sources` runs
(§6). The sandbox cannot reach any of these hosts.

| source | what it is | notes |
|---|---|---|
| Kalshi market `occurrence_datetime` | per-market field on every sports market | the candidate start |
| Kalshi market `expected_expiration_time` | Kalshi's own estimate of resolution | already taped as `hours_to_expiration` in `mmsell_candidate_ticks` and `execution_order_context` (decision time) |
| event ticker `YYMMMDDHHMM` | e.g. `KXMLBKS-26AUG081915CLECWS` = 19:15 ET | MLB and its props, cricket, esports, KBO/NPB. `0000` is a placeholder. Tennis, NFL, NCAAF and soccer carry the date only |
| event `product_metadata`, `sub_title` | event-level metadata | inspected, not used as a source |
| MLB StatsAPI `gameDate` | keyless public schedule | validation only |
| ESPN scoreboard (NFL, NCAAF FBS, MLS) | keyless public schedule | validation only |

**Pre-registered rule, per sport family** (MLB, NFL, NCAAF, soccer, tennis, other-sports):

1. Accept `occurrence_datetime` as the start when it agrees within **15 minutes** with a
   reference (ticker HHMM in ET, MLB StatsAPI, or ESPN) on **≥ 90%** of checkable events.
   This needs **≥ 20** checkable events.
2. With fewer than 20 checkable events, use the **internal test** instead. All three must hold:
   - `occurrence_datetime` is present on ≥ 90% of markets;
   - ≥ 95% of early-closed markets resolve after it;
   - the median lag to early resolution is ≤ 6h. (A day or tournament placeholder passes
     the ordering test and fails this one.)
3. Otherwise use the ticker HHMM (ET).
4. Otherwise the market is **uncovered**. It is counted in coverage and excluded from the
   measurement.

Reference APIs only validate a source. No P&L row takes its start from them.

### 2A. Amendment A (2026-10-15, before any P&L was read by entry time)

**Why it was amended.** The `--fields` run (ops `evtime-fields-20261015a`, 10 markets across
eight sport families) showed the rule above rested on a wrong premise.

**What `occurrence_datetime` actually is:**
- It equals `expected_expiration_time` on every sampled market.
- It is the **expected end** of the game, not the start:

| market | known start (UTC) | `occurrence_datetime` (UTC) | resolved (UTC) |
|---|---|---|---|
| MLB `KXMLBKS-26AUG081915CLECWS` | 23:15 (ticker 19:15 ET) | 02:15 (+3h) | 02:35 |
| MLB `KXMLBHR-26JUL261920NYYPHI` | 23:20 (ticker) | 02:20 (+3h) | 02:30 |
| NFL `KXNFLTOTAL-26AUG13INDNE` | 23:30 (expiration − 48h) | 02:30 (+3h) | 03:15 |
| NCAAF `KXNCAAFTOTAL-26AUG29UNCTCU` | 16:00 (expiration − 48h) | 19:00 (+3h) | 19:50 |
| tennis `KXATPCHALLENGERMATCH-26JUL13KENSHE` | — (date only) | 01:30 | **00:40, before it** |
| KBO `KXKBOGAME-26AUG110600LGKIW` | 10:00 (ticker 06:00 ET) | 14:00 (+4h) | 13:19 |

**Consequences:**
- Rule 1 would correctly have rejected `occurrence_datetime` as a start.
- Every date-only family (tennis, NFL, NCAAF, soccer) would then have been uncovered. Those
  hold most of the live sports sample.
- `expiration_time` also encodes the start, as start + a fallback. That fallback varies by
  series (48h, 54h, 72h, 14 days), so it is not used.

**Replacement rule.** Rules 1–4 above are superseded. Start precedence per market:

1. **Ticker HHMM (ET)** when present. This is checked against MLB StatsAPI, and agreement
   within 15 minutes is reported.
2. **External schedule** start when the event matches one: MLB StatsAPI, or ESPN for NFL,
   NCAAF FBS and MLS. Match by date plus both team codes. Doubleheaders resolve to the game
   nearest the ticker time.
3. **`occurrence_datetime − D`**, where D is the family's offset (occurrence minus known start)
   calibrated on events that rules 1–2 cover:
   - **Family D:** accepted with **≥ 20** such events and **≥ 80%** of offsets within
     **30 minutes** of the median.
   - **Pooled D:** a family without its own calibration borrows the D pooled across all
     families, only if the derived start passes the internal test. Both must hold:
     - ≥ 95% of its early-closed markets resolve after the derived start;
     - the median resolution lands 0–6h after it.
4. Otherwise the market is **uncovered**.

**Unchanged:** the sample (§3), the windows and the bar (§4), and the consequence (§5). The
reference APIs may now supply a start (rule 2), but they remain public and keyless.

**Disclosed limits:**
- Tennis will most likely rest on the pooled D. Tennis start times are themselves soft ("not
  before", order of play), so its in-play boundary is the least precise.
- The run reports how many live fills sit within ±30 minutes of the start. Those are the
  fills a start error can move across the in-play boundary.

## 3. Sample (STEP 2)

- **Priority 1, live:** every settled LIVE mmsell canary market that one strategy traded, with
  a fill. This is the same filter as ops `live-canary-markets-20261015a`: 2,870 markets,
  −$24.51.
  - Restricted to Kalshi series category `Sports`, excluding mentions.
  - Entry time = **first fill**.
  - c/contract = realized P&L / filled contracts.
- **Priority 2, paper:** `mmsell10` paper trades, settled, in the same sports series, on the
  **same event days** as the live sample. Entry time = paper entry.
- **Priority 3** (wider paper history) runs **only if** 1–2 show a signal.
- Only these markets are looked up on the API. No history backfill.

## 4. Measure and the bar (STEP 3)

- **Windows**, on hours from the event start to the entry:
  - pre-game > 24h;
  - pre-game 6–24h;
  - pre-game < 6h;
  - in-play (at or after the start).
- **Uncertainty:** a 90% interval from the day-clustered bootstrap (`boot_diff`, whole event
  days resampled) in `scripts/live_canary_patterns.py`.
- **SIGNAL** needs one window to clear all of these:
  - on live, its c/contract minus every other window pooled is **≥ 2.0¢** in magnitude;
  - that difference's 90% interval **excludes zero**;
  - the window holds **≥ 30** live markets;
  - on paper, with **≥ 30** entries in the window, the difference has the **same sign**.
- **Otherwise NO SIGNAL**, recorded and not retried without a new pre-registration.
- **Multiplicity, disclosed:** the four windows are not independent tests. A single bad
  window makes the others look good against the rest. The verdict names the window that
  carries the effect.

**Descriptive only (not gated):**
- the share of live fills that happened in-play;
- live orders placed before the start but first filled in-play ("rested into the game");
- fills after Kalshi's expected expiration;
- live minus paper per window;
- a per-family table;
- where the live "12–24h before close" cell (−5.6¢, 115 markets) sits against the real start.

## 5. Consequence

- **SIGNAL:** a one-paragraph proposal for a **paper** arm, for example cancelling resting
  orders at the start or skipping entries within X hours of it. Its own pre-registration and
  Experiment OS registration follow, and the owner decides whether to go further.
- **NO SIGNAL:** recorded. Timing is dropped as an explanation of the paper-to-live gap.
- **No live change from this work, either way.**

Code: `scripts/event_timing_study.py` (ops script). `--sources` prints STEP 1 with no P&L. The
default run prints everything.

## 6. Results

Pending: filled in after the run, below this line, without editing §1–5.
