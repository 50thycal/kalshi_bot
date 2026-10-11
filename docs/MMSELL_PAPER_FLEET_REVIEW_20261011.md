# MMSELL paper fleet review — which arms still answer an open question (2026-10-11)

**Session role:** Research Lab · **Context:** WS-024 (Railway resource budget) · **Operator:** Calvin
**Rule applied:** gates decide, not P&L. Every keep/retire call below cites the Experiment OS
object it rests on — an arm, a deployment, a gate and its recorded result — never a paper number
on its own. Nothing here changes a live tag, `LIVE_STRATEGIES`, a gate, or real-money exposure.

## Evidence (ops results on the `ops` branch, 2026-10-11)

| id | what it read |
|---|---|
| `mmv-ct-1`, `mmv-list-1` | Control Tower and experiment list |
| `mmv-cost-2` | paper opens/week per tag |
| `mmr-arms-1` | every active deployment arm of every non-retired experiment: role, tag, kind, twin link |
| `mmr-ceildeps-2` | every deployment (ended included) of the five `mmsell-price-ceiling*` experiments |
| `mmr-gates-1`, `mmr-holds-1` | gate specs and the latest recorded verdict and reason |
| `mmr-env-1` | live `MMSELL_VARIANTS` and `LIVE_STRATEGIES` (`Kmmsell10,Alimm1`) |
| `mmr-held-1` | real-money positions still read as held by the four stood-down live tags |

## How the evaluator reads tags (why the answer is what it is)

- A gate clause reads the tags of the deployments **of one kind** in the experiment's epoch
  (`evaluator._arm_scope`). Ended deployments still count; the window is the evidence window.
- **Keep gates** of the live canaries address `deployment_kind="live"` in every clause
  (`canary_mmsell10.KEEP_GATE_SPEC` and its successors). Live metrics read `live_orders`/fills
  for the live tag. The only `paper_trades` they read are the **twin** tags, resolved through
  `twin_of_deployment_id` (`metrics._twin_tags`).
- **Promotion gates** read `kind="paper"` deployments. For each canary that was its own
  `mmsell10` paper deployment, now ended (`mmr-ceildeps-2`). None carries a live tag.
- **External control:** `mmsell-type-tight`'s delta clause names `mmsell-price-ceiling` / arm
  `mmsell10`. That resolves to the latest version's epoch (v2/e2), whose paper deployment
  `mmsell-ceiling-paper-2-e2` carries the tag **`mmsell10`**. So `mmsell10` is the
  load-bearing control, not `Cmmsell10`.

## Per-tag recommendation and decision

Opens per week from `mmv-cost-2`. Decision column records the operator's answer in-session on
2026-10-11.

| Tag | Experiment · role | What it feeds | Opens/wk | Recommendation | Decision |
|---|---|---|---|---|---|
| `mmsell10` | cell-size v1/e2 paper arm | cell-size `paper_to_live_canary` (PASS recorded 10-11); type-tight's external control | 1311 | KEEP | keep |
| `Gmmsell0` | correlation-cap · **control** | `paper_keep` | 1311 | KEEP | keep |
| `Gmmsell1` | correlation-cap · treatment | `paper_keep` | 556 | KEEP | keep |
| `Rmmsell1` | reviewed-universe · **control** | `paper_keep` | 127 | KEEP | keep |
| `Rmmsell2` | reviewed-universe · treatment | `paper_keep` | 91 | KEEP | keep |
| `Tmmsell1/2/5/6` | type-tight · secondaries | `paper_keep` HOLD, recorded reason: sample floor (Tmmsell2 43 vs ≥ 100) | 664 | KEEP. The gate is still accruing | keep |
| `mmsell9` | price-ceiling v1/e2 · secondary | operator keep on XOS-000033: type filter and price ceiling together | 710 | KEEP | keep |
| `mmsell` | wide-control · **control** (no gate) | scan-funnel `in_band` telemetry reference (recorded 2026-08-12) | 2061 | KEEP | keep |
| `Kmmsell10` | cell-size · LIVE | live canary | 60 | out of scope | — |
| `Cmmsell10` | price-ceiling · **live** tag, stood down | nothing | 1311 | RETIRE via a Live Ops package | **retire (Live Ops package)** |
| `Dmmsell10` | capacity · **live** tag, stood down | nothing | 1311 | RETIRE via a Live Ops package | **retire (Live Ops package)** |
| `Fmmsell10` | contest-cap · **live** tag, stood down | nothing | 556 | RETIRE via a Live Ops package | **retire (Live Ops package)** |
| `Jmmsell10` | size-split · **live** tag, stood down (superseded by `Kmmsell10`) | nothing | 580 | RETIRE via a Live Ops package | **retire (Live Ops package)** |
| `mmsell5/6/7/8` | variants-2026-07 · secondaries | `paper_to_live_canary`, HOLD forever (below) | 3050 | RETIRE the whole experiment | **retired now** |

Retiring both groups removes about 6,800 of about 12,900 MMSELL paper opens per week.

### Q1 — the stood-down canaries' paper rows feed nothing, but the order of steps is forced

`Cmmsell10`, `Dmmsell10`, `Fmmsell10` and `Jmmsell10` are each the **live** deployment's tag
(`kind=live` in `mmr-arms-1`). They are not paper arms. The twins (`*_pt3`, `*_pt4`) are
derived from `LIVE_STRATEGIES`, so they stopped at stand-down. The paper books keep opening
because the tags are still in `MMSELL_VARIANTS`. No gate, twin or control reads those rows.

Two mechanisms forbid simply editing them out:

1. **Drift.** The live worker compares each open live deployment's registered `book_params`
   with `MMSELL_VARIANTS` (`enforcement._material_runtime_config`). A stand-down explains only
   the `LIVE_STRATEGIES` and twin differences (`_explained_by_stand_down`). A missing book is
   `EXPERIMENT_CONFIG_DRIFT`, which blocks the keep gate of a book that still settles real
   money.
2. **No verb.** `STAND_DOWN` refuses an experiment that holds an open live deployment
   (`OPEN_LIVE_DEPLOYMENT`). `RETIRE_ON_GATE_FAIL` refuses a live experiment, and none of
   these gates has failed. Ending a live deployment exists only inside a reviewed package
   (`service.end_deployment`).

**Decided order (Live Ops owns it):**

1. A reviewed package ends the four live deployments and their twin deployments once each
   book's real positions have settled.
2. `STAND_DOWN → RETIRED` for capacity, contest-cap and size-split.
3. **Do not retire `mmsell-price-ceiling`.** Its v1/e2 still carries `mmsell9` (the
   XOS-000033 keep), and a RETIRED experiment above an open epoch is the XOS-000011 shape.
4. Remove the four specs from `MMSELL_VARIANTS` (read the live value, remove, set).

**Drain status (`mmr-held-1`):** every position still read as held for C, D and F is on a
market that settled in Aug–Sep. This is a defect, now **XOS-000045** (DATA, owner Live Ops, opened by
command `rl-phantom-held-20261011`). The `executor.py` settlement loop writes the qty=0 row only for settlements
dated on the current UTC day, so a missed day leaves the last non-zero snapshot as "newest"
forever. C/D/F are drained in reality. `Jmmsell10`'s 10 positions are real (about $20.50)
and settle by about 2026-10-16.

### Q2 — why `mmsell10`, `Cmmsell10`, `Dmmsell10` and `Gmmsell0` open identical trades

All four run `lo=5,hi=10,maxyes=7`. `C` and `D` add `size=1`, which changes order size, not
selection. The same is true of `Fmmsell10` and `Gmmsell1` (both `contestcap=1`).

- **`mmsell10` + `Gmmsell0`: by design.** A tag carries one active arm.
  `mmsell-correlation-cap` needs a control in its own epoch and snapshot; an external control
  across snapshots is what left `mmsell-anchor-vol-entry` stuck in BLOCKED_PLATFORM
  (`config.py`, contest-cap block).
- **`Cmmsell10` + `Dmmsell10`: redundant.** They are leftovers of stood-down canaries
  (Q1).

### Q3 — `mmsell-variants-2026-07`: no open question its contract can still answer

- **Gate:** `paper_to_live_canary` has `pass_all: realizable_cents_per_trade > 0` per arm. It
  has **no fail clause and no horizon**.
- **Recorded HOLD (10-04, system):** mmsell6 at −0.297c and mmsell7 at −0.54c realizable.
  Every sample floor is met.
- **Why it can never move:** the realizable projection depends on the 2026-08-11 fill
  calibration and each arm's fixed price band. More paper rows cannot change it, and a future
  recalibration would re-project the rows already recorded. So v1 can neither PASS (while 6
  and 7 are ≤ 0) nor FAIL.
- **Nobody else needs these arms:** no arm is a control, twin or external control for any
  other experiment (`mmr-arms-1`; the only active external control is type-tight → `mmsell10`).
- **The surviving question has another carrier.** mmsell5's question (type slice positive
  after the fill model) continues as `mmsell9` (the same types plus the ceiling). A future
  promotion attempt for mmsell5 alone would need a new Version with fresh evidence, not this
  one.

**Executed 2026-10-11:**

1. `STAND_DOWN → RETIRED`, command `rl-retire-variants-20261011b`, `approved_by` Calvin. It
   wrote no verdict. Receipt SUCCEEDED at 22:58:26Z: PAPER → RETIRED, epoch 1 closed,
   `mmsell-variants-paper-legacy-1` ended. The first attempt (`rl-retire-variants-20261011`)
   was REJECTED and changed nothing: the experiment transport caps each payload string at 256
   characters.
2. After that receipt, the four specs were removed from `MMSELL_VARIANTS` (ops
   `mmr-env-set-1`, VERIFIED). The value was computed from a fresh read of the live value
   (`mmr-env-2`, identical to `mmr-env-1`). The other 15 books, including the live
   `Kmmsell10` spec, are byte-identical.

### Q4 — each experiment's control

| Experiment | Control | Kind |
|---|---|---|
| mmsell-correlation-cap | `Gmmsell0` (arm `uncapped`) | in-experiment |
| mmsell-reviewed-universe | `Rmmsell1` (arm `reviewed_uncapped`) | in-experiment |
| mmsell-type-tight | `mmsell10`, via mmsell-price-ceiling v2/e2 arm `mmsell10` | external |
| mmsell-wide-control | `mmsell` (it is the reference) | self |
| mmsell-variants-2026-07 | none: `control_required=False`, absolute realizable bar | — |
| mmsell-price-ceiling* canaries | the registered paper twin (`*_pt3`/`*_pt4`) for execution; `mmsell10` for the paper edge | twin / paper |

None of the retired tags is a control.

## Not in scope, recorded so it is not lost

- `mmsell` (wide-control) feeds no gate. It is kept on its recorded telemetry rationale; the
  operator re-confirmed this on 2026-10-11.
- `mmsell-type-tight`'s gate has no fail clause either. It is still accruing toward its sample
  floor, so it was left alone. Revisit when Tmmsell2 reaches n = 100 (about 11 weeks at the
  current rate).
