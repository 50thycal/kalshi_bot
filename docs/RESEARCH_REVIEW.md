# Research review — every 72 hours

**What this is:** the standing procedure for the Research Lab's 72-hour review, asked for by the
owner on 2026-11-01. The 4-hour check-in (`docs/handoffs/HANDOFF-research-checkin.md`) asks
whether the research is still collecting data. This review asks what the research should do
**next**. It authorizes nothing outside research scope. Experiment OS stays canonical.

**Scope (owner, 2026-11-01):** the checker for **every** experiment, in both lines:
- the **incentive programme** (`Alimm1`, liquidity rewards);
- the **MMSELL strategy** (the `mmsell` books).

**Goal:** raise realized net profit (`DEC-039`).
- **Incentive programme:** better markets (more reward net of fills per market-day), fewer or
  cheaper fills and better exits, plus new features and data sources.
- **MMSELL:** a better edge or universe, fill protection, and profit per contract.

## The caps: running experiments per line (owner, 2026-11-01; MMSELL raised 2026-11-03; incentive raised 2026-11-08 to 8, then 10)

There are two separate caps: **10 for the incentive programme** (raised from 5 to 8 on 2026-11-08, DEC-043, then to 10 the same day, DEC-044) and **20 for MMSELL** (raised
from 5 by the owner on 2026-11-03). Slots do not have to be used.

- **Counts in its line:**
  - every pre-registered research read that is accruing or waiting on its date (the
    `scheduled` rows in `docs/RESEARCH_CALENDAR.md`, plus the forward reads under "Other
    standing routines");
  - every open Experiment OS experiment that is actually running a book: a `PROBE`, `PAPER`
    or `LIVE_CANARY` experiment with a tag that traded in the last 7 days.
- **Does not count:**
  - instrumentation such as the shadow collector;
  - the scorecard and check-in routines;
  - an experiment that is stood down and on its way to being closed.
- **At or over the cap in a line:** no new experiment starts in that line. If it is over, the
  review recommends what to close. Ideas wait on the candidate list.
- **Below the cap:** start at most **one** new experiment per line per review, and only one
  that passes the screen in step 5.

## Procedure (each run)

1. **Re-arm first:** `update_trigger` with `run_once_at` = now + 72 h and `enabled: true`.
2. **Inventory (read-only, through the ops channel, unique ids):**
   - `xos control-tower`;
   - `xos show liquidity-incentive-mm`, and `xos show` for any experiment whose state is unclear;
   - a `db` read of `paper_trades` and `live_orders` per tag over the last 7 days (which books are
     really running);
   - `docs/RESEARCH_CALENDAR.md`;
   - the newest file in `docs/liquidity_incentive/scorecards/` and in `docs/mmsell/scorecards/`;
   - the top of `docs/RESEARCH_JOURNAL.md`;
   - the last review file in `docs/research_reviews/`.
3. **Each running experiment** (counts only, never an early read):
   - **accruing** → one line with its progress and its date;
   - **due or past its date** → confirm its routine ran, its PR merged, and its calendar row reads
     `DONE — <verdict>`. If not, flag it.
   - **done** → state what its verdict means for the programme: nothing, a follow-up read, or a
     nomination. A nomination is an **owner decision**, written as one plain question.
4. **Close-out candidates.** List experiments in Experiment OS that are finished but still open,
   for example stood down but still `LIVE_CANARY`. A close-out or stand-down is a lifecycle
   move, so it is **recommended**, never done here. Live ones belong to Live Ops with the
   operator's approval.
5. **New experiment?** Only in a line below its cap. Screen that line's candidate list, plus any
   new idea from the `kalshi-idea-model` skill aimed at that line's goal above, on these tests:
   - **novel** against what was tried (`LIMM_PROGRAMME_OUTCOMES.md` §2, the placement runs,
     `LIMM_FILL_HAZARD.md`, the thesis §9 log; for MMSELL, `docs/RESEARCH_JOURNAL.md`,
     `docs/BOOK_REGISTRY.md` and the graveyard verdicts; never revive a killed family without
     a new premise);
   - **read-only and cheap,** on data that exists or a collector already running;
   - **falsifiable,** with bars fixed before any read;
   - **able to change a decision** the owner would actually make.
   - **If one passes,** build it in the same run, request-authorized as research scope
     (`DEC-012`): pre-registration doc, probe script, tests, ops allowlist entry, calendar row
     and a fresh-session read routine (pattern: the `Kalshi:` routines), then a PR, merged when
     CI is green.
   - A probe never changes the live book. A rule change it motivates is a nomination.
6. **Record:** write `docs/research_reviews/YYYY-MM-DD.md` (under 40 lines) and keep the
   candidate list below current. Open a docs PR and merge it when CI is green.
7. **Tell the owner,** briefly and in plain words (owner preference, 2026-11-01): one line if
   nothing needs them; otherwise up to four lines, with each decision as a plain question.

Hard limits: no env change, lifecycle move, gate verdict, arming or exposure change. Nothing
pre-registered is read before its date. No model identifiers in anything pushed.

## Candidate list (waiting for a slot; one line each)

Ordered by expected value to the goal. A candidate is not work until a review starts it.

### Incentive programme

- **Reward attribution without screenshots:** rewards are confirmed only from the owner's
  screenshots. A data source that reads payouts directly, such as the balance residuals in
  `incentive_balance_observations` matched to programme end dates, would make every review's
  net exact. This is an instrument, so the build is owned by Live Ops or a workstream.
- **Per-channel YouTube risk:** YouTube views became the loss source (scorecard 2026-10-31).
  Study 5 scores `KXYTVIEWSHIGH` as a series. If it reads `FILL-TRAP`, a per-channel split is
  the follow-up.

- **Volume-programme bursts:** 33 volume programmes worth $17.5k ran from 09-19 to 10-29, and none
  are live now (study 7). Watch for the next burst, then read what they pay for and whether our
  flow earns them.

- **Narrow fill-warning trigger:** in study 8's backtest, a 5-minute price range of 2¢ or more
  preceded a fill 5 times in 10. A new pre-registered test of that trigger alone, or combined
  with a print, is a candidate.

### MMSELL

- **NFL team props re-run** (lookalike §19: +3.8¢ on 36 entries) once the season adds ~100
  entries. NHL periods have no cell; MLB innings wait for spring.
- **Untraded-capacity shortlist, remaining:** the commodity/FX 15-minute markets (a new
  mechanic, not an MMSELL cell).
- **Pooled state-data family** (monthly state jobs, permits, port, rent and visitor ladders,
  such as KXWAAEROEMP, KXCABUILDPERMITS, KXNYNJPORTTEU): slow information, P-SLOW's thesis.
  Screened 2026-11-04 and not built. There is no local history, and §12's outside-weather
  replays entered the cell only about 2–3% of the time. Revisit if a cheap count of settled
  markets shows more than about 800 across the family.
- **BTC quiet-hour crossing-side selling:** VOLCLOCK found the hourly `KXBTCD` crossing side
  dear outside the US event hours (+2.71¢ ungated, post hoc; `VOLCLOCK_PREREG.md`). This is
  tail-selling, so it needs its own pre-registration on data after 2026-11-06. Earliest about
  4 weeks of new data. Coordinate with the session that owns VOLCLOCK.
