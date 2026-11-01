# Research review — every 72 hours

**What this is:** the standing procedure for the Research Lab's 72-hour review, asked for by the
owner on 2026-11-01. The 4-hour check-in (`docs/handoffs/HANDOFF-research-checkin.md`) asks
whether the research is still collecting data. This review asks what the research should do
**next**. It authorizes nothing outside research scope. Experiment OS stays canonical.

**Goal (owner, 2026-11-01):** improve the liquidity-incentive programme (`Alimm1`) by any of:
- **better markets:** more reward net of fills per market-day;
- **avoiding or handling fills better:** fewer fills, cheaper fills, better exits;
- **anything else that raises its net,** including new features and new data sources.

The scoreboard is the north star (`DEC-039`): realized profit after fees, plus confirmed rewards.

## The cap: at most 5 research experiments running

- **Counts:** every pre-registered research read that is accruing or waiting on its date, in
  any book. These are the `scheduled` rows in `docs/RESEARCH_CALENDAR.md` plus the forward
  reads listed under "Other standing routines".
- **Does not count:** the live and paper books themselves (`Alimm1`, the `mmsell10` canaries),
  instrumentation such as the shadow collector, and the scorecard and check-in routines.
- **At the cap:** a new idea goes on the candidate list below and waits. Nothing is started.
- **Below the cap:** start at most **one** new experiment per review, and only one that
  passes the screen in step 3.

## Procedure (each run)

1. **Re-arm first:** `update_trigger` with `run_once_at` = now + 72 h and `enabled: true`.
2. **Inventory (read-only, through the ops channel, unique ids):**
   - `xos control-tower`;
   - `xos show liquidity-incentive-mm`;
   - `docs/RESEARCH_CALENDAR.md`;
   - the newest file in `docs/liquidity_incentive/scorecards/`;
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
5. **New experiment?** Only below the cap. Screen the candidate list, plus any new idea from the
   `kalshi-idea-model` skill aimed at the goal above, on these tests:
   - **novel** against what was tried (`LIMM_PROGRAMME_OUTCOMES.md` §2, the placement runs,
     `LIMM_FILL_HAZARD.md`, the thesis §9 log);
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

- **Fill early-warning:** do snapshot features before a fill predict it, such as
  `trades_last_5m`, `price_range_5m_cents` or depth at best thinning? If so, a "pull the bid"
  rule. It needs study 5's fills first.
- **Volume-incentive programmes:** `incentive_type = 'volume'` exists in `incentive_programs`
  but has never been studied. A census of what they pay, and for what, is a new avenue.
- **Reward attribution without screenshots:** rewards are confirmed only from the owner's
  screenshots. A data source that reads payouts directly, such as the balance residuals in
  `incentive_balance_observations` matched to programme end dates, would make every review's
  net exact. This is an instrument, so the build is owned by Live Ops or a workstream.
- **Per-channel YouTube risk:** YouTube views became the loss source (scorecard 2026-10-31).
  Study 5 scores `KXYTVIEWSHIGH` as a series. If it reads `FILL-TRAP`, a per-channel split is
  the follow-up.
