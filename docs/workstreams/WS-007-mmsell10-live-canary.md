# WS-007 — A fresh mmsell10 live canary with an exact paper twin

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-08-28
**Updated:** 2026-08-28

## Goal

Put the `mmsell10` arm of `mmsell-price-ceiling` on real money as a Stage-1 canary with a
fresh paper twin created at the same instant — built, tested and reviewable in one PR, and
stopping short of every action that expands real-money exposure.

## Context

The brief asked for a canary registered against `mmsell-price-ceiling` at its *current*
version and epoch, using the `mmsell10` arm alone. Experiment OS refuses that shape, for two
independent structural reasons that were read off production and are now reproduced as tests
rather than asserted:

- `arm_live_canary` requires a pre-registered risk envelope on the version (`risk_json`).
  v1 has none and froze on 2026-08-16; the flush guard refuses every edit to a frozen
  version, because the approved envelope is part of the contract.
- `arm_live_canary` requires the live and twin tag maps to equal the declared arm set
  exactly. v1 declares `mmsell9` alongside `mmsell10`, so a canary on v1 would have to put
  the negative-paper arm on real money too.

A changed arm set is a new Version by the system's own rule, and a risk envelope can only be
pre-registered on one. So the successor Version is not a workaround — it is what registering
this canary means here. Its cost is real: evidence windows floor at the epoch start, so v2's
evidence restarts at zero and the recorded PASS cannot be inherited. The operator accepted
that on 2026-08-28 and chose to register v1's bar with no floor.

Separately, this workstream builds the measurement contract the canary is judged on. Five
keep/stop quantities the brief requires had no canonical provider (fill rate, open exposure,
worst realized loss, tail-loss count, risk-gate blocks), plus total realized live P&L, which
is the only unit a loss *budget* can be denominated in.

## Current Mental Model

```text
  mmsell-price-ceiling                        (state: PAPER)
    v1 [FROZEN 2026-08-16]  arms {mmsell9, mmsell10}   risk_json: NONE
      e1  snapshot 5c3720fca2fe36f0 (MARKET_TAXONOMY coverage_2026_08_13)
        mmsell-ceiling-paper-legacy-1  -> mmsell9, mmsell10
        paper_to_live_canary: PASS recorded 2026-08-23  (n=1588 on mmsell10)
                             ^ cannot authorize: wrong arm set, no envelope

  ── the package registered, T = 2026-08-28T04:11:45.750998Z (DONE) ──────

    v1/e1  mmsell-ceiling-paper-legacy-1 ENDED at T
           mmsell-ceiling-paper-mmsell9-1 -> mmsell9        (keeps that book alive)
    v2 [FROZEN at T]  arms {mmsell10}     risk_json: Stage-1 envelope
      e1  snapshot 4f9adf15daa64035 (the ACTIVE one)
        mmsell-ceiling-paper-2      -> mmsell10             (evidence restarts here)
        paper_to_live_canary  (v1's bar VERBATIM — no evidence floor)
        live_canary_keep      (pre-registered, every clause kind='live')

  ── then, on a separate approval ────────────────────────────────────────

      e2 [I2]  arm_live_canary at ONE instant:
        mmsell-ceiling-live-1  kind=live        -> Cmmsell10
        mmsell-ceiling-twin-1  kind=paper_twin  -> Cmmsell10_pt3  twin_of -> live

  ── and only then, separately again ─────────────────────────────────────

      MMSELL_VARIANTS  += Cmmsell10:lo=5,hi=10,maxyes=7,size=1
                                  <- CREATES the book. Without it the tag below
                                     names nothing and book_params drifts.
      <the 13 envelope settings>  <- pinned, so the contract is true of the process
      LIVE_STRATEGIES=Cmmsell10   <- the switch that lets an order reach Kalshi
                                     (one env call: all of it, one redeploy)
```

The `mmsell10` tag hand-over is the part most easily got wrong: a tag resolving to two
ACTIVE deployment arms is refused as ambiguous, so leaving the v1 two-arm deployment active
alongside a v2 deployment on the same tag would have stopped the paper book. Ending a
deployment does not orphan its evidence — metric scopes resolve tags over every deployment in
the epoch, ended or not; only the enforcement resolver reads `ended_at`.

## Decisions Made

- **A successor Version, not an epoch.** Forced by the two refusals above, both reproduced in
  `tests/test_mmsell10_canary_package.py`. Recorded in `change_reason` on v2.
- **The arm is carried across verbatim.** `lo=5, hi=10, maxyes=7`, same universe, entry
  timing, sizing, settlement, fee model and order type. A test asserts v2's params equal v1's.
- **No crypto exclusion.** It would be a different universe and could not inherit this arm's
  evidence. Crypto is a reported monitoring slice only.
- **Full order book stays authoritative for `maxyes`.** The quote pre-filter stays disarmed;
  `tests/test_mmsell_orderbook_authoritative.py` proves a 41c-wrong inline quote cannot admit
  a market the book refuses, and that an armed pre-filter silently drops real candidates.
- **The tail-loss stop is structural, not invented.** Under a one-contract clip a settled
  market cannot lose more than ~$1, so `live_max_realized_loss_usd > 1.0` is a stand-down;
  cumulative tail cost is bounded by the loss budget. No tail-count threshold is registered,
  because there is no evidence from which to choose one.

## Decisions Taken (operator, 2026-08-28)

All six are answered; nothing on this workstream is blocked on the owner any more.

- **D1. Successor Version — ACCEPTED**, and with it the evidence restart.
- **D2. Promotion sample floor = 0.** v1's literal contract; the proposed 300 was declined.
  The gate can therefore clear on a thin fresh sample — read `fill_model_coverage_pct` and
  the sample behind the projection at arming time, because the gate will not.
- **D3. Win-rate stand-down 5.0pp** (the registered 1.0pp stays a promotion bar).
- **D4. Decision-overlap hold 50%, fill-rate hold 25%.**
- **D5. Loss budget $15, daily stop $5.** Exposure limits apply to the positions this canary
  opens; existing holdings are ignored, so `MAX_TOTAL_EXPOSURE` is left where production has
  it (100) rather than tightened around ~$17 of legacy stood-down holdings.
- **D6. `mmsell-type-tight`'s control reference** moves to v2/e1 as declared. Accepted.
- **Naming latitude granted** — used, twice (below).

## Open Decisions

None. Three findings surfaced while applying the decisions and were fixed under the naming and
scoping latitude rather than referred back:

- **The activation step could not run, and did not name the book.** Seven of the variables it
  sets were absent from `railway_env.ALLOWED_VARS` — the same defect class as #266, found by
  audit this time rather than by an operator mid-procedure. The blocking one is
  `MMSELL_VARIANTS`: a live mmsell book is an ordinary entry in that string, so
  `LIVE_STRATEGIES=Cmmsell10` alone names a book that does not exist, and the plan's step 4
  did not mention it at all. The other six are mmsell's concentration safeguards and the
  pre-filter, which production leaves unset — so they hold `config.py` defaults that today
  happen to equal what the envelope declares, which is luck, not a contract. Fixed in
  `DEC-006`: all seven allowlisted, the request composed from the running value by
  `scripts/mmsell10_canary.py activate` rather than typed, and `activation_vars` asserted
  against the allowlist in CI for every registered package.

- **The twin tag was wrong.** Production carries `LIVE_PAPER_TWIN_SUFFIX=_pt3`, and the
  runtime DERIVES the twin tag from it. The registered `Cmmsell10_pt` would have meant the
  twin wrote rows under `Cmmsell10_pt3` — a tag with no active deployment arm, refused at the
  write path under `NEW_ONLY`. The canary would have armed with a twin that could record
  nothing. Renamed to `Cmmsell10_pt3`, suffix pinned in the envelope, derivation pinned by
  test, and a changed suffix is now detected as drift.
- **The clip was a process-wide setting.** `MAX_ORDER_SIZE` is not watched by the
  config-drift detector and would have capped every book sharing the process. Moved to the
  book's own `size=1` inside `mmsell_variants`, where it rides in the drift-checked
  `book_params` — so raising the clip later is detected. `LIVE_EXIT_MODE` was dropped for the
  same class of reason: production carries `tp_sl` for the YES/weather books, and mmsell holds
  to settlement structurally.

## Assumptions

- The applied I0/NO_ACTION disposition for `mmsell-price-ceiling` on
  `MARKET_TAXONOMY:settlement_repair_2026_08_24` still stands at arming time, so the
  synchronous re-evaluation is not refused for snapshot staleness.
- Production still carries `LIVE_PAPER_TWIN_SUFFIX=_pt3` and `MAX_TOTAL_EXPOSURE=100` when
  the canary is armed (both read 2026-08-28). A changed suffix is drift-detected; a changed
  exposure cap is not, and would only ever refuse new entries.
- `LIVE_STRATEGIES` names this canary alone while it runs, so the envelope's process-wide
  settings have no other consumer.

## Non-Goals

- Changing shared metric semantics to make a gate pass. Nothing here is a Platform Revision;
  the new providers implement quantities the registry did not yet have.
- Reviving `mmsell-scheduled-settle-live` or `theta4-fat-tail`, whose successor contracts were
  withdrawn on 2026-08-21 (`#251`). That withdrawal turned on treatment and control differing
  in universe, entry band and settle mode at once — a deconfounding problem a single-arm
  canary does not have.
- Arming the pre-filter, adding a crypto exclusion, or touching the runtime allowlist.

## Build Card

Inline: register a single-arm successor contract with a pre-registered Stage-1 envelope and a
pre-registered keep/stop gate; implement the six missing live providers; prove the order book
is authoritative for the price ceiling; hand over the `mmsell10` tag without ambiguity; and
stop before arming.

## Implementation State

**Merged ([#264](https://github.com/50thycal/kalshi_bot/pull/264)):**
`kalshi_bot/experiment_os/canary_mmsell10.py` (contract, envelope, gates, registration and
arming), six new providers in `metrics.py`, `scripts/mmsell10_canary.py` (operator entry
point, dry-run by default), `scripts/mmsell_canary_slices.py` (crypto monitoring, allowlisted
read-only). Deployed and verified healthy; inert until a package is registered.

**Merged ([#265](https://github.com/50thycal/kalshi_bot/pull/265),
[#266](https://github.com/50thycal/kalshi_bot/pull/266)):**
`kalshi_bot/experiment_os/experiment_commands.py` — the
`EXPERIMENT_OS_EXPERIMENT_COMMAND` transport, so registration and arming can reach production
without an operator's own writable connection; #266 fixed the allowlist entry that made it
unreachable through its own channel.

**REGISTERED IN PRODUCTION, 2026-08-28T04:11:45.750998Z** (receipt `mm10-register-2`,
SUCCEEDED, `executed: true`). What that instant did, read back rather than asserted:

- v2 frozen, single arm `mmsell10` with `lo=5, hi=10, maxyes=7` identical to v1's, and the
  Stage-1 `risk_json` present (v1's is a JSON `null`).
- v2/e1 open on snapshot `4f9adf15daa6…`, the ACTIVE one, carrying
  `MARKET_TAXONOMY:settlement_repair_2026_08_24`.
- `paper_to_live_canary` (spec `f15ea2a7bfb93f24`) and `live_canary_keep` (`4a15a90fba5e1365`)
  registered, evidence started at the epoch instant.
- The tag hand-over completed with no ambiguity: `mmsell-ceiling-paper-legacy-1` ended at the
  same instant, `mmsell-ceiling-paper-mmsell9-1` opened on v1/e1 for `mmsell9`, and
  `mmsell-ceiling-paper-2` opened on v2/e1 for `mmsell10`. `readiness` reports 2 native
  deployments, 0 resolver-degraded alarms, 0 unresolved integrity events.
- Gate state on zero fresh evidence is exactly as designed: `paper_to_live_canary` HOLD,
  `live_canary_keep` BLOCKED_DATA (live-only clauses with no live deployment — missing, not
  zero). **Arming would be refused today**, and correctly so.

**In review:** the activation defect below.

## Review State

Operator decisions applied 2026-08-28. Nothing is registered and nothing is armed; the
runtime live allowlist is empty and the ops channel is `noop`.

The gap the second PR closes: registration and arming are **writes**, and the ops channel is
read-only against Postgres by design — a SELECT-only role, enforced server-side. So the merged
package could only be run by an operator on their own connection. The two sibling transports
(`EXPERIMENT_OS_ISSUE_COMMAND`, `EXPERIMENT_OS_PLATFORM_COMMAND`) already solve exactly this
shape for their own domains, and this is the third, keeping "the worker is the only writer"
intact rather than widening the ops channel.

## Related Decisions

`DEC-001` (the authority boundary), `DEC-004` (a narrowed arm set or envelope is a successor
Version), `DEC-005` (the lifecycle transport names a reviewed package and cannot author one),
`DEC-006` (book definitions are settable through the ops channel, and an activation request is
composed rather than typed).

## Related PRs

[#264](https://github.com/50thycal/kalshi_bot/pull/264),
[#265](https://github.com/50thycal/kalshi_bot/pull/265),
[#266](https://github.com/50thycal/kalshi_bot/pull/266) (all merged) and this PR.

## Next Step

Let `mmsell-ceiling-paper-2` accumulate fresh evidence. The promotion gate is registered
UNFLOORED (the operator's `D2`), so it can clear as soon as `realizable_cents_per_trade` is
positive on any evidence at all — which makes reading `fill_model_coverage_pct` and the sample
behind the projection an *operator* step, because the gate will not do it. Arming remains a
separate approval, and the runtime allowlist a separate one after that.
