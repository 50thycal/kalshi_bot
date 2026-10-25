# HANDOFF — record the LIMM rule changes in Experiment OS (2026-10-25)

**Owner role:** Live Ops (real-money book; the formal path is a hard stop under `DEC-012`).
**Decision needed from the operator first:** which option below. Nothing here is authorized yet.

## What changed outside Experiment OS

The live book `Alimm1` (experiment `liquidity-incentive-mm`, `LIVE_CANARY`, twin `Alimm1_pt3`)
has run rule changes edited straight into `kalshi_bot/liquidity_incentive/live.py` and
`runner.py`, each recorded in `docs/LIQUIDITY_INCENTIVE_THESIS.md` but not as an XOS object:

| § | Date (UTC) | Change |
|---|---|---|
| 9.55–9.56 | 2026-10-15 | event-tied mention markets only; 10 markets / $100 / 3 per event |
| 9.60–9.62 | 2026-10-17 | resting sells on a filled leg |
| 9.63–9.64 | 2026-10-19/20 | bids step aside 48h before the event; fill contagion; 24h runway |
| 9.65–9.68 | 2026-10-23/24 | plumbing fixes (dust, orphaned orders, gone-from-exchange) |
| 9.69 | 2026-10-24 ~17:23Z | rank by expected reward; $20 bids at 4c+; `KXYTVIEWSHIGH` added |
| 9.70 | 2026-10-25 ~15:01Z | cancel the rest of a bid once it starts to fill |

The registered risk envelope (`kalshi_bot/experiment_os/liquidity_incentive_mm.py`
`RISK_ENVELOPE`) tracks the running constants, but the deployment's frozen `config_json` from
arm time does not.

## Why this is not a bookkeeping step

Thesis §9.30 (2026-09-19) tried exactly this and found the wall: closing the open epoch and
carrying deployments forward is refused for live deployments —
`only ['paper'] deployments may be re-registered automatically`. Live lineage can only be
created by `service.arm_live_canary`. Recording an epoch therefore means:

1. a reviewed package (code change) in the shape of `mmsell-contestcap-epoch2` /
   `recut_mmsell10_contest_cap`: close the open epoch, open its successor, register live + twin
   deployments on **fresh tags** (e.g. `Blimm1` / `Blimm1_pt3`);
2. standing `Alimm1` down (cancel resting bids; held positions follow kill-switch semantics);
3. `ARM_CANARY` through `EXPERIMENT_OS_EXPERIMENT_COMMAND` with `approved_by` naming the
   operator — **hard stop, real-money expansion path**;
4. a `LIVE_STRATEGIES` change to the new tag — **hard stop**;
5. updating `LIVE_TAG`/`TWIN_TAG` in `live.py` and every reference (reward ledger, livedash,
   check-in prompt).

## Options

- **A. Keep the precedent (recommended for now).** Thesis §9.30/§9.34 record the operator's
  standing choice: rule changes on this book are noted in the thesis, and that note is the
  boundary. Add one line to the thesis listing the boundaries above (done in §9.69/§9.70) and
  treat evidence before/after each § as non-poolable. Cost: none. Risk: XOS stays behind the
  book's real rules.
- **B. Formal re-cut.** Steps 1–5 above, in a Live Ops session, after the current bids roll
  off (no held positions is the clean moment). Cost: a day of build + review, a stand-down,
  reset of in-memory state (reward memory, cooldowns). Do it when the book's rules have
  settled, not mid-iteration.

## Acceptance (option B)

`xos show liquidity-incentive-mm` shows the new epoch open with live + twin deployments on the
fresh tags; the old tags have no working orders; the bot places on the new tag; the reward
ledger and check-in read the new tag.
