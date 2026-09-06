# mmsell contest-cap live canary — the pre-registered plan

The operator-facing contract for putting the mmsell cheap-band book on real money with its
concentration bound **corrected**, and an exact paper twin. Written **before** arming, so
nothing in it can be chosen after seeing a result.

Experiment OS is canonical for everything this document describes. Where the two disagree,
Experiment OS wins and the disagreement is a bug here. This document does not restate a
standing, a gate verdict or a P&L figure.

Code: `kalshi_bot/experiment_os/successor_mmsell10_contest_cap.py`. Package:
`mmsell-contest-cap-canary`. Audit: `scripts/mmsell_contest_cap_audit.py`. Acceptance
evidence: `tests/test_successor_mmsell10_contest_cap.py`.

---

## 1. THIS IS NOT A PROMOTION. `contestcap=1` IS NOT A TREATMENT.

State this correctly or the whole framing is wrong.

The live risk envelope this canary inherits **already** carries `max_event_rungs: 3`. Nobody
A/B tested that number; it is a bound, pre-registered as one, alongside the $1/order cap and
the 40-open cap. What XOS-000020 established is that it counts `event_ticker`, which is
**series × contest** — so it caps 3 rungs per *listing*, not per *game*. One MLB game is up
to five listings (`KXMLBTOTAL`, `KXMLBTEAMTOTAL`, `KXMLBSPREAD`, `KXMLBHR`, `KXMLBF5TOTAL`),
so a book may legally hold ~15 correlated positions on nine innings and no cap notices.

`max_contest_positions: 1` is the **correction to that existing bound**. It can only ever
REFUSE an entry, never add one, so it moves real-money exposure in the safe direction only.

Consequently this package **registers no new decision rule**. The promotion bar and the
keep/stop contract are the predecessor's own frozen objects, *imported* rather than retyped
(`tests/…::test_both_gates_are_the_predecessors_objects_not_retyped_copies` asserts
`is`-identity, so a relaxed threshold is not a one-character edit away — it is impossible).
If you find yourself designing a promotion criterion for a risk limit, stop and re-read this
section.

### The evidence — the prior, not a result

* Live, replayed on `Dmmsell10`'s own 97 fills, keeping the first N per contest:

  | rule | actual | cap 4 | cap 3 | cap 2 | cap 1 |
  |---|---|---|---|---|---|
  | realized USD | −3.87 | −3.22 | −0.49 | −0.09 | **+0.43** |

* Paper replay over `mmsell10`, n=3,296 across 35 settlement days:

  | rule | ¢/trade | worst day | daily mean/sd |
  |---|---|---|---|
  | control | +0.649 | −$9.49 | 0.218 |
  | contest **grouping** only | +0.739 (+0.09) | **−$9.56 (unchanged)** | **0.153 (worse)** |
  | cap **every** correlation unit at 1 | +1.259 (+0.61) | −$2.31 (−76%) | 0.326 |

**Read the second row carefully.** The cross-series sports grouping the mechanism was *named*
for is the half that does nothing. The value comes from `regimes.contest_key_of` falling back
to the event ticker outside `CONTEST_GROUPED_REGIMES`, which tightens every other ladder from
3 rungs to 1. Both halves arrive from the one knob at `cap=1`. **Do not "improve" this by
restricting grouping to sports** — that keeps the half that measured nothing and discards the
half carrying the effect.

All of it is post-hoc replay on paper fills, with no maker adverse-selection haircut. It sets
the prior. It is not a result, and **nothing here is gated on it.**

## 2. Why a successor and not a re-arm

`service.arm_live_canary` requires state `PAPER`, and `LIVE_CANARY → PAPER` is an illegal
rollback. `mmsell-price-ceiling-capacity` is already `LIVE_CANARY`, so it has no sanctioned
path to re-arm with fresh tags and a corrected envelope. Neither rule may be worked around:
the PAPER guard is what stopped the 2026-08-15 inherited-state failure, and the rollback ban
is what keeps history honest. The lifecycle names the way out itself — a revived concept
creates a successor referencing its predecessor.

| | |
|---|---|
| predecessor | `mmsell-price-ceiling-capacity` (LIVE_CANARY) |
| successor | `mmsell-price-ceiling-contest-cap` |
| arm | `mmsell10` (unchanged) |
| paper control tag | `mmsell10`, handed over at the instant the predecessor's PAPER deployment ends |
| live tag | `Emmsell10` (fresh) |
| twin tag | `Emmsell10_pt4` (fresh; **derived** from the global `LIVE_PAPER_TWIN_SUFFIX`) |
| book spec | `lo=5,hi=10,maxyes=7,size=1,contestcap=1` |

Only the predecessor's **PAPER** deployment ends, because two active deployment arms on one
tag is ambiguous and refused by the resolver. Its LIVE and TWIN deployments are **left open**
so `Dmmsell10` drains with every settlement still recording — ending a live deployment leaves
its tag without an arm, which is the XOS-000011 blackout shape.

`Gmmsell1` is deliberately **not** claimed even though it is the contest-capped paper book: it
carries the active treatment arm of `mmsell-correlation-cap`, a running paper experiment with
a 60-settlement-day floor. It keeps running, untouched, as the paper prior this canary rests
on.

## 3. The risk envelope — the predecessor's, plus exactly one line

| limit | value | vs. predecessor |
|---|---|---|
| contracts per order | 1 | same |
| exposure per market | $1.00 | same |
| **positions per CONTEST** | **1** | **NEW — the correction** |
| correlated rungs per event | 3 | **left at 3, deliberately** |
| exposure per event | $3.00 | same |
| open positions | 40 | same |
| book exposure (implied) | ~$39.60 | same |
| twin open cap | 250 | same |
| events per correlated settlement date | 5 | same |
| positions per settlement date | 25% of the book cap | same |
| daily realized-loss stop | $5.00 | same |
| total canary loss budget | $15.00 | same |
| order timeout | 4h, then cancel | same |
| entry price | no-bid + 0c | same |
| exit | hold to settlement | structural |

`max_event_rungs` **stays at 3 and is not swapped out**: the contest cap is the *tighter*
bound and still leaves the rung cap binding per event ticker for anything the contest key does
not group. Removing it would be a second change in a step that is supposed to carry one.
`tests/…::test_the_envelope_differs_from_the_predecessors_by_the_cap_and_the_stage` asserts
structurally that `max_contest_positions` is the **only** key that differs, so a second change
smuggled in beside the correction fails CI rather than review.

### The global-switch check (`.claude/sessions/live-ops.md`)

The variables this canary's activation sets are listed in `ACTIVATION_VARS`. Of the *global*
mmsell knobs:

* **`MMSELL_CONTEST_CAP_ENABLED` is NOT set and stays `false`.** `tracker.py` is shared by
  every mmsell book, so turning the global on would re-scope `mmsell5`–`10`, the `Tmmsell`
  family, `Lmmsell`, and the running `Gmmsell0`/`Gmmsell1` control at one instant — a
  shared-semantic change belonging to Platform Change Review, and under `NEW_ONLY` a contract
  change nobody registered. The **per-book `contestcap=1` override wins over the global pair**
  (`tracker.py`: `cap_n = contest_cap if contest_cap is not None else …`), so this book opts
  in alone and no other book's selection moves. It is not in `railway_env.ALLOWED_VARS`
  either, so the channel could not set it.
* **`LIVE_PAPER_TWIN_SUFFIX` stays `_pt4`.** It is global; changing it would orphan every
  other live book's twin tag, which then resolves to no deployment arm and goes dark under
  `NEW_ONLY` (the XOS-000011 shape). The twin tag is **derived** from it, never chosen against
  it.
* `MMSELL_LIVE_MAX_OPEN_POSITIONS`, the rung-cap and settlement-cap knobs are already at the
  envelope's values in production and are re-declared, not changed.

Books that could be in `LIVE_STRATEGIES` while this runs: `Cmmsell10` (ceiling canary,
stood down), `Dmmsell10` (capacity canary, stood down), `Lmmsell8`/`Lmmsell10` (stood down),
`theta4`. As of the arming preflight `LIVE_STRATEGIES` is **empty**; if that changes,
reconcile before adding exposure.

### `mmsell_live_min_tier` — the bar you get whether you want it or not

It defaults to `graduated` (PR #338) and is **not in the ops allowlist**, so the canary runs
with that bar on and it cannot be turned off through the channel. It only ever refuses a live
entry, so that is the safe direction.

**It is not a substitute for this cap and it does not bound the worst case.**
`KXNFLSPREAD` **is** in the graduated manifest and has lost **$166.55 on n=382**. The tier bar
limits *which series are eligible*; the contest cap limits *how many correlated rungs one bad
contest can take*. They are orthogonal, and only the second one bounds a single-game pile-up.

## 4. The keep/stop contract

Carried **verbatim** from `mmsell-price-ceiling` v2 via the capacity successor — the same
object, not a copy. Summarised (authoritative text: `canary_mmsell10.KEEP_GATE_SPEC`):

| outcome | mechanism |
|---|---|
| insufficient evidence → keep running | `sample`: `live_settled_contracts ≥ 150`; horizon 600 → `HORIZON_EXHAUSTED` |
| strategy loss → stop | `live_realized_pnl_usd ≤ −15.0` from 20 settled contracts |
| accounting failure → stop | `|twin_live_paired_gap_cents| > 0.5` from 30 contracts (both signs) |
| envelope not applied → stop | `live_max_realized_loss_usd > 1.0` from 1 contract |
| win-rate divergence → stop | `twin_live_winrate_gap_pp > 5.0` from 50 contracts |
| uninterpretable → HOLD, never PASS | `twin_mirror_coverage_pct < 50`, `live_fill_rate_pct < 25` |

Parameter drift and stale twin evidence are handled **structurally** — `runtime_config_check`
records `EXPERIMENT_CONFIG_DRIFT` and the evaluator refuses any verdict; twin metrics return
MISSING, never zero, giving `BLOCKED_DATA`.

The cap rides inside the drift-checked `book_params` (`contestcap=1` in the live book spec),
so **editing it out of `MMSELL_VARIANTS` mid-canary is recorded as drift and takes the keep
gate to `BLOCKED_INTEGRITY`** rather than silently removing a real-money bound.

## 5. Preflight — all must pass, all read-only

| # | check | how |
|---|---|---|
| 1 | deployed sha contains `c4b2ce1` | the ops result header prints the sha |
| 2 | `mode NEW_ONLY`, 0 unresolved integrity events, 0 config drift | `{"type":"xos","command":"enforcement"}` |
| 3 | `Gmmsell0`/`Gmmsell1` are COLLECTING (not zero rows) | `paper_trades` group by strategy |
| 4 | `skipped_contest_cap` is incrementing | `system_events` where `component='mmsell_scan'` |
| 5 | current `LIVE_STRATEGIES` reconciled | `{"type":"env"}` |
| 6 | `Emmsell10` / `Emmsell10_pt4` have **zero** `paper_trades` history | freshness; `arm_live_canary` refuses otherwise |

`xos control-tower` currently **hangs** (a known narrow defect, run 33996384294). Use
`enforcement`, `readiness`, `scoreboard`, `tag`. The rest of the read path is healthy; this is
not a broad ops outage.

Prerequisite behind check 1: `c4b2ce1` (PR #335) gave the contest cap its own whole-open-book
read, `repo.open_positions_contest_summary`, deliberately **not** settlement-date scoped.
Before it, an MLB game starting after ~18:30 ET had its F5 legs before UTC midnight and its
full-game legs after, so they counted against two different days' budgets and the cap did not
fire — **silently**, because `skipped_contest_cap` simply stayed 0, which reads as "nothing to
refuse". Arming without it ships a cap that cannot bind on exactly the late games the drawdown
came from.

## 6. Arming — two separate acts, deliberately

**A. `ARM_CANARY`** through `EXPERIMENT_OS_EXPERIMENT_COMMAND`. Restricted to
`actor_role=LIVE_OPS`. `approved_by` must name the operator who actually approved it — never a
session id, a model name, or "claude".

```json
{"command_id":"…","action":"ARM_CANARY","actor":"…","actor_role":"LIVE_OPS",
 "payload":{"package":"mmsell-contest-cap-canary","approved_by":"<operator>"},
 "schema_version":1}
```

A successful `ARM_CANARY` **places no order** and leaves the allowlist exactly as it was.

**B. The runtime allowlist** — `MMSELL_VARIANTS` (which CREATES the book) plus
`LIVE_STRATEGIES=Emmsell10`, in one `env` call. This is the act that starts real trading and
it is its own decision with its own confirmation. `LIVE_STRATEGIES` matches by **prefix**;
`Emmsell10` is prefixed by no existing tag and prefixes none but its own twin, which
`LiveExecutor._allowed` refuses real orders outright.

**Never hand-compose `MMSELL_VARIANTS`.** It is one ~1,100-character string holding every
book; retyping it to add one entry is how a running book gets dropped silently. Read it first
(`{"type":"env"}`) and append the single `;Emmsell10:lo=5,hi=10,maxyes=7,size=1,contestcap=1`
entry to the value that read returned.

Registering does not make the canary armable: `arm_live_canary` re-evaluates the promotion
gate synchronously, and immediately after registration `realizable_cents_per_trade` is
undefined. The successor earns its own paper evidence in its own window first.

## 7. The verification loop

Cadence: every ~30 min for the first 4 hours (one mmsell scan cycle is a 30-min ride-along),
then hourly. Each iteration, **stop at the first red**:

1. **Kill conditions, before any "is it working" question.** Realized live P&L ≤ the $5 daily
   stop; any single market's exposure > $1.00 (structurally impossible at a 1-contract clip,
   so it means sizing is wrong); total at-risk > the ~$39.60 ceiling; unresolved integrity
   events > 0. **Stand down** = clear the tag from `LIVE_STRATEGIES`. That stops NEW entries;
   open positions still settle and are still real money. Reducing exposure never needs
   permission; adding it always does.
2. **Is it trading at all?** `select count(*) from live_orders where strategy='Emmsell10'`.
   Zero after two full scan cycles is a **failure, not patience** — that exact shape (armed,
   configured, silent) is what wasted 12 hours on the paper arms.
3. **Is the cap firing?** `{"type":"script","name":"mmsell_contest_cap_audit","args":
   ["--live","Emmsell10","--uncapped","mmsell10"]}`. The audit imports the worker's own
   `contest_key_of` by file path rather than re-implementing the key in SQL, and it reports
   `BREACH` / `HELD` / `UNPROVEN`. **`UNPROVEN` is a real outcome**: if no book anywhere held
   2+ on one contest, the cap had nothing to refuse and a clean live max proves nothing yet.
   It separately reports whether a **UTC-straddling** contest has occurred — until one has,
   the `c4b2ce1` fix is unexercised in production and must not be reported as verified.
4. **Twin parity.** `{"type":"script","name":"live_paper_parity"}`. A twin at low coverage is
   not an execution control no matter what a threshold says.
5. **Gate state.** A recorded FAIL is a stand-down, not a discussion.

Never, in the loop: widen the envelope, raise a cap or add exposure to "get more data";
re-interpret the gate after seeing results; treat a zero counter as evidence the cap is
unnecessary — it is more likely broken, and that has now happened twice on this exact
mechanism.

## 8. Rollback and stand-down

* **Stop new entries:** `{"type":"env","set":{"LIVE_STRATEGIES":""}}` — or `KILL_SWITCH=true`
  for the portfolio. Held positions continue to settle and are still real money. The twin
  stands down with live, because twin pairs derive from `LIVE_STRATEGIES`.
* **Undo the runtime config:** revert `MMSELL_VARIANTS` by removing the one `Emmsell10:`
  entry, never by clearing the variable — clearing it drops the service back to the code
  default and silently undoes every other book set through the channel.
* **The registration does not roll back, and should not.** A frozen version, a registered gate
  and a recorded transition are append-only by design. Retiring the canary is a recorded
  lifecycle move, never a deletion.

## 9. The five silent failures this line of work has already produced

Each looked green. Assume the next one does too.

1. the cap keyed the wrong unit (`event_ticker`, i.e. series × contest);
2. the UTC-midnight straddle (`c4b2ce1`);
3. the paper books registered in Experiment OS but never present in the worker config — 12
   hours of "armed, configured, silent";
4. a counter that overstated;
5. a test suite that passed 81 green without exercising the fix at all.

An honest "armed but not yet provable" beats a confident overstatement. If a check was
skipped, name it and say why.
