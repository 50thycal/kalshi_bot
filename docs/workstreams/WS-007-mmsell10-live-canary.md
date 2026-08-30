# WS-007 — A fresh mmsell10 live canary with an exact paper twin

**Phase:** REVIEW
**Status:** Active — LIVE
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
  mmsell-price-ceiling                  (state: LIVE_CANARY as of 14:20Z)
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

  ── ARMED 2026-08-28T14:20:35.572574Z (operator-approved) ───────────────

      e2 [I2]  arm_live_canary at ONE instant:
        mmsell-ceiling-live-1      kind=live        -> Cmmsell10
        mmsell-ceiling-twin-1      kind=paper_twin  -> Cmmsell10_pt3  twin_of -> live
        mmsell-ceiling-paper-2-e2  kind=paper       -> mmsell10
                                  ^ the paper parent, CARRIED onto the live
                                    epoch. Without it (WS-008) arming would have
                                    ended its deployment and blocked the very
                                    book the canary was promoted from.

  ── and only then, separately again ─────────────────────────────────────

      MMSELL_VARIANTS  += Cmmsell10:lo=5,hi=10,maxyes=7,size=1
      <the 13 envelope settings>
      LIVE_STRATEGIES=Cmmsell10
```

**ACTIVATED 2026-08-28T14:48Z.** All 16 variables set in one call; the first
redeploy was refused by a Railway deployment rate limit and retried, so the caps
and the switch landed together on the boot at 14:49:31Z. First live order
14:46:26Z on the preceding cycle's config load; by 14:56Z the book held 20 live
orders (2 filled, 18 resting), 38 `Cmmsell10` paper rows and 20 on the twin.
First settlement was +$0.0689 on BOTH sides — a paired gap of 0.0c against a
0.5c stand-down bar, which says the comparison is wired to the same market, and
nothing yet about the edge.

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

## What it armed on, stated plainly

The promotion gate PASSED at 14:08Z on **n = 2 settled trades**:
`realizable_cents_per_trade` **+1.345c**, `fill_model_coverage_pct` **100%**, bar `> 0`.

That number is not a measurement of what those two trades earned — it is their ENTRY-PRICE
MIX projected through the live fill calibration. Both entered at 6c and 7c yes-equivalent,
cells measured from real mmsell3 fills at **+1.77c** and **+0.92c**; their mean is exactly
1.345c. So the gate said *the prices this book is buying at have historically filled
profitably*, not *this book made money twice*. Sturdier than two P&L outcomes, and still only
two markets' worth of price selection.

n was 2 rather than 1,588 for two compounding reasons: evidence never pools across epochs, so
v2/e1 started empty at 04:11Z; and the mmsell family was dark until 13:30Z (XOS-000011), so
only ~40 minutes of entries existed and only two had resolved. The operator's `D2` chose the
unfloored bar knowingly, precisely so v2 would not have to re-earn v1's n=1588. The honest
reading: **the promotion rests on v1's history, not on v2's fresh sample.**

## Operator decisions, 2026-08-30 (carry these into the NEXT run)

Two things this canary cannot fix about itself, decided and recorded so the
successor Version does not inherit them by accident.

**D7. The `twin_mirror_coverage_pct < 50` HOLD stands unfixed for this run.**
It reads 14.7% and cannot clear. The cause is structural: the twin assumes 100%
fill, so it saturates its 20-position open cap while live fills ~49% and keeps
placing (twin open 20, live open 13; the parity report reads *"live placed 147
orders its twin did NOT open"*). A registered gate clause is immutable from
registration, so the only fixes were a successor Version (restarting evidence at
zero) or retuning the twin's cap mid-epoch (voiding the comparison). Neither is
worth it while the book is profitable and every stop clause works. **For the next
run:** either set the threshold against what a fill-limited twin can actually
achieve, or give the twin a larger open cap than live so it is never the binding
constraint. The clause as written measures the twin's cap, not the mirror.

**D8. Raise `MMSELL_LIVE_MAX_OPEN_POSITIONS` (currently 20) — but NOT mid-run.**
The 34.5% decision overlap is gate-dominated, mostly `gate:open_cap`, which is
capacity rather than edge: live is declining candidates it was never allowed to
attempt. Raising the cap buys more of the SAME distribution, which is the safe
way to scale — unlike pricing up, which `mmsell10a`/`mmsell10b` measured at
−4.1c/contract for +3pp of fill rate. It is deliberately not done now: the cap is
a live knob inside the twin's parameter set, and retuning it mid-epoch voids the
twin comparison — the fix would be a new twin tag, not a re-read of this one
(`docs/LIVE_PAPER_TWIN.md`). **For the next run:** raise it in the pre-registered
envelope, before arming, so the whole epoch runs at one cap.

**Both are capacity levers and neither touches entry pricing.** The 0c offset and
the 4h timeout stay: the 56 timed-out orders are trades the market declined at our
price, and buying them is the one thing already measured to destroy this edge.

## XOS-000014 — the 31% of entry orders Kalshi never accepted

The `cancel_reason` was truncated in every view I had read it through. In full it is
a 404 on `/portfolio/events/orders`: `user_not_found`, *"Exchange user not found. For
Predictions: reference documentation Exchange Sharding documentation."* The refusals
are per-series and binary, not load-shaped — `KXMLBHR` 22/22, `KXMLBTOTAL` 20/20,
`KXMLBSPREAD` 12/12, `KXITFWMATCH` 4/4, `KXITFMATCH` 3/3, `KXBTCD` 5/6, and **0/130
across the other seventeen series**.

Kalshi has sharded its exchange. This codebase has no concept of a shard: one
`kalshi_base_url`, every order posted to it. Their doc names two requirements, and
they are *different problems with different owners*:

1. **Routing.** `exchange_index` rides on `GET /markets` and `GET /events` and is
   "the authoritative source of truth". As an order parameter, `>= 0` routes to that
   exchange and `-1` auto-routes from the market ticker. We send neither.
2. **Collateral.** *"Programmatic traders must preallocate collateral on a given
   exchange shard before order placement."* Balance is held per shard; `Get Balance`
   breaks it down by index, and Kalshi can auto-rebalance to a target allocation.

**Why no routing code ships yet.** If we are unfunded on the shard MLB/ITF/BTCD live
on, correct routing still fails — that is an operator funding decision about real
money, not a code change. And adding an unrecognised field to the order body risks
the one path that currently works (130 attempts, 0 rejections) on a live real-money
book. So a read-only probe (behind `LIVE_SHAPE_PROBE`) reports both halves against
our own account first: which index the refused series carry versus the accepted
ones, and which indexes our balance reaches. Balance **amounts are never logged** —
these lines come back through the ops channel onto a public branch.

**Scale of the prize.** 76 of 246 entry attempts were refused before reaching the
book. This is not maker queue position — the corrected fill-rate provider already
reports them as `excluded_never_sent`, correctly declining to blame the strategy for
orders the venue never took. Fixing it is worth more than any pricing change, and
costs no edge.

### Measured, 2026-08-30T18:05:05Z — `exchange_index` is the whole split

The probe ran against our own live account and the answer is binary and complete:

| series | verdict | `exchange_index` |
|---|---|---|
| `KXMLBHR`, `KXMLBTOTAL`, `KXITFMATCH` | REFUSED | **3** |
| `KXNCAAFSPREAD`, `KXLALIGASCORE` | ACCEPTED | **0** |

No other candidate field varied (`market_type` is `binary` on both sides; the only
key-set difference, `primary_participant_key`, is a tennis-vs-football artifact and
appears on the refused side only because ITF markets name a player). **We trade on
shard 0. MLB and ITF live on shard 3.**

The same boot showed `exchange_index` on `orders`, `fills`, `positions` and
`settlements` too — so a fix has to reach the reconciler, not just order placement,
or we would file shard-3 fills against shard-0 assumptions.

Also measured: `api limits probe` reports our grant as
`{"exchange_instance": "event_contract", "level": "advanced"}` — the grant itself is
**scoped to an exchange instance**, which is consistent with `user_not_found` rather
than an insufficient-funds error on shard 3.

**A defect in the probe's own funding half.** It reported *"no per-index breakdown in
balance payload"* — a **false negative**. The breakdown is right there under
`balance_breakdown`, as the neighbouring `api shape probe [balance]` line printed in
the same boot: `{"balance_breakdown": [{"balance": "str", "exchange_index": "int"}]}`.
The first version looked up four guessed key names and none was the real one. Two
lessons, both now enforced by tests: find the breakdown **by shape** (any list whose
entries carry `exchange_index`), and treat a **decimal string** as money, since a
numeric-only test would report every funded shard as unfunded — wrong in the
direction that looks safe.

### Answered, 2026-08-30T18:20:49Z — unfunded, not misrouted

The fixed probe read our own balance breakdown:

```
shard probe funding: [{"exchange_index": 0, "funded": true},
                      {"exchange_index": 1, "funded": false},
                      {"exchange_index": 2, "funded": false},
                      {"exchange_index": 3, "funded": false}]
```

The account carries a row for every shard 0-3, so we are **provisioned** on shard 3
and hold **no collateral** there. `user_not_found` is what Kalshi returns for an
unfunded shard — the message names a user, the condition is a balance.

**This is not fixable in code.** Routing `exchange_index` correctly to shard 3 would
still be refused; the order would simply be rejected after being addressed properly.
Had routing shipped on the first pass it would have compiled, passed, deployed and
changed nothing. Only an intra-account transfer of collateral onto shard 3 — an
operator action on real money — unlocks those series.

**It also explains part of the headline live-vs-twin gap.** The twin trades MLB and
ITF markets that live is structurally locked out of, so the +2.22c twin against
+1.51c live is not all edge decay. The matched-markets comparison already controlled
for it and read **-0.07c**; that number now has a mechanism behind it.

**Recorded:** XOS-000014 evidence row 30 (`ADD_EVIDENCE`, receipt
`xos14-shard-evidence-20260830`, SUCCEEDED).

**D9 (next run, NOT mid-epoch).** Either fund shard 3 in the pre-registered envelope
before arming, or drop the shard-3 series from the universe so the book stops
spending ~31% of its attempts on markets it cannot reach. Narrowing the universe now
would void the twin comparison for the same reason as D8: the universe is inside the
twin's parameter set. Whichever is chosen belongs in the envelope before arming, and
the twin must be given the same universe live can actually trade — otherwise the
mirror keeps measuring a book live is not allowed to run.

## Next Step

Watch the pre-registered keep/stop clauses accumulate. `live_canary_keep` reads BLOCKED_DATA
until `live_settled_contracts` reaches 150 — correct, not a fault; these markets settle over
days. Nothing here needs a new threshold: the stand-downs (loss budget $15, daily $5, paired
gap 0.5c both signs, per-market loss $1, win-rate 5.0pp) were all registered before any
result was seen, and re-interpreting them now would void the pre-registration. Arming remains a
separate approval, and the runtime allowlist a separate one after that.
