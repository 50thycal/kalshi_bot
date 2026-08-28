# mmsell10 Stage-1 live canary — the pre-registered plan

The operator-facing contract for putting `mmsell-price-ceiling`'s `mmsell10` arm on real
money with an exact paper twin. Written **before** arming, so nothing in it can be chosen
after seeing a result.

Experiment OS is canonical for everything this document describes. Where the two disagree,
Experiment OS wins and the disagreement is a bug here. This document does not restate a
standing, a gate verdict or a P&L figure — read those with
`{"type":"xos","command":"control-tower"}`.

Code: `kalshi_bot/experiment_os/canary_mmsell10.py`. Operator entry point:
`scripts/mmsell10_canary.py`. Acceptance evidence:
`tests/test_mmsell10_canary_package.py`.

---

## 1. Why a successor Version exists

The canary was asked for at `mmsell-price-ceiling`'s current version and epoch. Experiment
OS refuses that shape for two independent reasons, both reproduced as tests against the real
`arm_live_canary` rather than asserted here:

| refusal | why it cannot be worked around |
|---|---|
| v1 carries no `risk_json` | `arm_live_canary` requires a pre-registered envelope; v1 is frozen and the flush guard refuses every edit to a frozen version |
| v1 declares `mmsell9` **and** `mmsell10` | live and twin tag maps must equal the declared arm set exactly, so arming v1 would put the negative-paper arm on real money |

A changed arm set is a new Version by the system's own rule (`add_arm` says so on a frozen
version), and an envelope can only be pre-registered on one.

**What that costs.** Evidence windows floor at `max(epoch start, gate evidence start)`, so
v2's evidence starts at zero and the recorded PASS on v1/e1 cannot be inherited. There is no
shape that keeps both the old PASS and a one-arm canary.

The operator's decision of 2026-08-28 is to register v1's bar with **no evidence floor**
(n = 0) — v1's literal contract, which records no explicit `n`. A 300-trade floor was
proposed and declined. What n = 0 means in practice: the gate can clear on a thin fresh
sample rather than a rebuilt one. It is not satisfiable on nothing —
`realizable_cents_per_trade` is MISSING until a trusted fill-calibration cell covers the
book's price mix, and MISSING is `BLOCKED_DATA`, not a pass — but **read the arming-time
value against `fill_model_coverage_pct` and the sample behind it before approving the arm.
The gate will not do that for you.**

## 2. What is carried across unchanged

`lo=5`, `hi=10`, `maxyes=7`; the market universe; entry timing; sizing logic; settlement
behaviour (hold to settlement, no TP/SL); the fee model; the order type (post-only resting
maker NO-buy); risk semantics. v2's declared arm params are asserted equal to v1's.

**No crypto exclusion is added.** Excluding a market class would be a different universe —
a different Version — and could not inherit this arm's evidence. Crypto is a reported
monitoring slice and nothing more (§7).

## 3. The Stage-1 risk envelope

Pre-registered on the version, so it freezes with the contract. Sizing arithmetic: the book
buys NO at `100 − yes_ask` and `maxyes=7` caps the yes side at 7c, so one contract costs
93–99c and a $1.00 per-order cap yields exactly one contract at every admitted price.

| limit | value | enforced by |
|---|---|---|
| contracts per order | 1 | the book's own `size=1` (in `mmsell_variants`, so drift-checked) through `order_quantity`, under the `LIVE_MAX_ORDER_DOLLARS` belt |
| exposure per market | $1.00 | `LiveExecutor._market_exposure` → `gate:exposure` |
| correlated rungs per event | 3 | mmsell event-rung cap → `skip_event_rung_cap` |
| exposure per event | $3.00 | the rung cap × the clip |
| open positions | 20 | `repo.count_live_book_open` → `gate:open_cap` |
| book exposure (implied) | ~$19.80 | 20 × one clip |
| events per correlated settlement date | 5 | `skip_event_cap` |
| positions per settlement date | 25% of the book cap | `skip_settlement_cap` |
| daily realized-loss stop | $5.00 | `LiveExecutor._daily_loss_hit` → `gate:daily_loss` |
| **total canary loss budget** | **$15.00** | *not* a runtime breaker — the keep gate's `live_realized_pnl_usd` clause, actioned by an operator stand-down |
| order timeout | 4h, then cancel | `LIVE_ORDER_TIMEOUT_SECONDS` |
| entry price | no-bid + 0c (join the queue) | `MMSELL_LIVE_PRICE_OFFSET_CENTS` |
| exit | hold to settlement | structural for mmsell — not a setting (the TP/SL knobs are YES-side only) |

### What this envelope deliberately does not touch

Three settings were in an earlier draft and were removed after reading production on
2026-08-28, because their blast radius is wider than this canary:

- **`MAX_TOTAL_EXPOSURE`** — portfolio-wide, and by operator decision the canary limits the
  positions it opens, not money other books already hold. Production carries **100**, so the
  canary's own ~$19.80 ceiling binds first with ample room over the ~$17 of legacy
  stood-down holdings. Tightening it around those holdings would size a *shared* breaker to
  one book's needs; loosening it would weaken a live safeguard to make this canary easier to
  arm. It still applies as a shared backstop: if total open exposure ever reaches the cap,
  this canary is refused **new entries** alongside every other book — a fail-safe, and exits,
  closeouts and reconciliation always run regardless.
- **`MAX_ORDER_SIZE`** — process-wide, and *not watched by the config-drift detector*. The
  clip is set per book as `size=1` inside `mmsell_variants`, where it rides in the
  drift-checked `book_params`, so raising it later is detected.
- **`LIVE_EXIT_MODE`** — production carries `tp_sl`, which is correct for the YES/weather
  books and is what their held positions exit under. mmsell holds to settlement
  structurally, so forcing `settlement` would change another family's exits and buy this
  canary nothing.

**Stand-down semantics.** Emptying `LIVE_STRATEGIES` stops new entries on the next cycle;
resting orders drain within a cycle; **held positions keep exiting and settling and remain
real money**. Enforcement records `EXPERIMENT_EXECUTION_STOOD_DOWN` (informational,
non-blocking) rather than config drift, so evidence gathered before the pause stays
interpretable. The twin stands down with live, because twin pairs derive from
`LIVE_STRATEGIES` — which is what preserves the one-to-one property.

No existing safeguard is weakened. Every cap above is either production's current value or
tighter, and the three settings this envelope declines to touch are listed above.

## 4. The pre-registered keep/stop contract

Registered as the `live_canary_keep` kill gate before arming, and immutable from then on:
the flush guard freezes a gate's spec at registration and refuses any edit once evidence
starts. Every clause names `deployment_kind: "live"` explicitly — including the evidence
horizon, which defaults to `paper` and would otherwise take the whole gate to `BLOCKED_DATA`
on a live-only epoch. That is exactly the defect that leaves
`mmsell-scheduled-settle-live` unjudgeable today.

| outcome | mechanism |
|---|---|
| **1. insufficient evidence** → keep running inside the envelope | the KEEP gate's own `sample`: `live_settled_contracts >= 150`, and `max_evidence_horizon` at 600 → `HORIZON_EXHAUSTED`, never an auto-verdict. (This floor is the keep gate's; the *promotion* gate has none — §1.) |
| **2. execution / accounting failure** → stand down and investigate | early-safety `fail_any` clauses carrying their own `min_evidence`, so a failure at a fraction of the promotion sample stops the book instead of sitting at HOLD |
| **3. strategy loss** → stop | `live_realized_pnl_usd <= -15.0` from 20 settled contracts |
| **4. successful evidence** → eligible for HUMAN review | `pass_all`; a PASS authorizes nothing |

The category-2 clauses, and why each threshold is what it is:

- **matched-market accounting**, `|twin_live_paired_gap_cents| > 0.5` from 30 contracts.
  Both signs trip. A gap on markets *both* sides settled cannot be fill rate or adverse
  selection — we got the trade — so it is our own arithmetic, and it invalidates paper gates
  on every book, not just this one. 0.5c is the repository's registered ALIGNED tolerance;
  Lmmsell10 measured ~0.40c inside it.
- **single-market severity**, `live_max_realized_loss_usd > 1.0` from 1 contract. Structural,
  not chosen: under a one-contract clip a settled market cannot lose more than one clip. If
  one does, the envelope is not being applied.
- **win-rate divergence**, `twin_live_winrate_gap_pp > 5.0` from 50 contracts. An operator
  choice (§8) — the registered 1.0pp is a *promotion* bar and is used as one below.

`hold_if` blocks a PASS without asserting failure, for the two conditions that make the
comparison uninterpretable rather than bad: decision overlap below 50%
(`twin_mirror_coverage_pct`) and fill rate below 25%. A low overlap is usually **capacity**,
not edge — `live_blocked_entries` carries the per-gate breakdown that says which — so it must
never read as a reason to stop.

Two conditions the brief lists are handled **structurally**, which is stronger than a clause:

- **unexpected parameter drift** — `runtime_config_check` records `EXPERIMENT_CONFIG_DRIFT`
  and the evaluator then refuses to render *any* verdict over the drifted deployment;
- **stale or missing twin evidence** — every twin metric returns MISSING, never zero, and the
  evaluator returns `BLOCKED_DATA`.

And one is handled by the envelope rather than an invented number: **excessive tail losses**.
At a one-contract clip each tail costs at most one clip, so 15 net-losing clips *is* the loss
budget. A separate tail-count threshold would be a number chosen with no evidence behind it.

## 5. The live/twin pair

| | |
|---|---|
| live tag | `Cmmsell10` |
| twin tag | `Cmmsell10_pt3` (DERIVED — see below) |
| live deployment | `mmsell-ceiling-live-1` (kind `live`) |
| twin deployment | `mmsell-ceiling-twin-1` (kind `paper_twin`, `twin_of` → live) |
| boundary | one instant, the new I2 epoch's `started_at`, identical on both |

Both tags are fresh: no `paper_trades` history before the arming instant and no active
deployment arm, or `arm_live_canary` refuses by name. The `C` prefix is this generation's
marker, as `L` was the last one's — `LIVE_STRATEGIES` matches by **prefix**, so a tag
beginning `mmsell10` would be captured by an allowlist entry naming the paper parent. The
twin is additionally refused real orders outright by `LiveExecutor._allowed`.

`mmsell10_pt`, `mmsell10_pt3` and `Lmmsell10_pt3` are ended historical evidence and are never
reactivated. A twin tag is single-use.

**Parameter drift is detected on either side.** The deployment records a `material` block
naming the live tag's `mmsell_variants` params, the twin pairing, and the allowlist
membership; `runtime_config_check` recomputes all three from the running Settings at boot.
The twin book is built as `dict(parent)` with only the tag replaced, so it has no independent
parameters and *cannot* drift alone — but both tags are recorded anyway, so a future refactor
that gave the twin its own spec would be caught rather than silently permitted.

## 6. Authoritative pricing

The `maxyes=7` decision reads the **full order book** (`best_no_bid` from
`compute_metrics(market, orderbook)`), never the event page's inline quote. The parity study
measured why: inline and book disagree by more than 5c on 0.6% of markets, with observed ask
discrepancies above 40c on BTC/ETH contracts. Against a 7-cent ceiling that is not a near
miss.

The inline-quote pre-filter stays **disarmed** (`MMSELL_PREFILTER_ENABLED=false`, the
default, and the state the applied I0 platform disposition explicitly relies on).
`tests/test_mmsell_orderbook_authoritative.py` pins both halves: a 41c-wrong inline quote
cannot admit a market the book refuses, and an armed pre-filter silently drops a real
candidate — which would change the live book's candidate stream relative to its twin, the one
contamination this comparison cannot survive.

Every entry decision's authoritative quote is already recorded: `live_paper_parity_events`
carries `yes_mid`, `no_bid`, `no_ask` from the fetched order book alongside the price each of
the three actors used, bounded per cycle.

## 7. Crypto monitoring — diagnostic only

`scripts/mmsell_canary_slices.py` (ops-allowlisted, read-only) reports `crypto`,
`non_crypto` and `unclassified` separately: settled markets, contracts, win rate, realized
c/contract, total realized dollars, tail-loss count and worst loss, open positions and
exposure, fill rate and decision overlap.

Slices are decided by an **exact whole-series match**, never a substring: `KXHEGSETHANNOUNCEOUT`
contains "ETH" and is a politics contract. That is XOS-000009's failure mode, and a
mis-sliced report is worse than a mis-scoped skip list because it invites a decision.
Markets whose series is unknown land in `unclassified` and are reported as such.

The previous generation's crypto slice was **12 settlements**. That is a small negative
signal worth watching, not a result — and the catastrophic loss in this family (Lmmsell8,
−$19.24 over 22 crypto settlements) came from a different strategy specification, not from
this arm and not from the shared execution engine. None of this is a stopping criterion
unless it is pre-registered as one before arming, and it is not.

## 8. Thresholds that are operator choices

Repository precedent was used wherever it exists. These had none and were decided by the
operator on **2026-08-28**:

| choice | decided |
|---|---|
| successor Version, and the evidence restart it implies | accepted |
| promotion sample floor | **0** — v1's literal contract; the proposed 300 was declined |
| win-rate stand-down | 5.0pp (the registered 1.0pp stays a promotion bar) |
| decision-overlap hold / fill-rate hold | 50% / 25% |
| total canary loss budget / daily stop | $15 / $5 |
| portfolio exposure | not set by this envelope — see §3 |
| naming | latitude granted; the tags stand, and a future collision bumps the generation number rather than reusing a historical name |

`scripts/mmsell10_canary.py register` prints them with their reasoning;
`canary_mmsell10.OPERATOR_DECISIONS` is the machine-readable record.

## 9. The sequence, and what each step does not do

1. **`register --execute`** — creates v2, registers both gates, freezes, opens v2/e1 on the
   active snapshot, and hands the `mmsell10` tag from v1's two-arm deployment to v2's.
   *Registers a contract. Arms nothing, places nothing.*
2. **wait** — until v2/e1's promotion gate clears its floor. `arm` refuses on HOLD.
3. **`arm --execute --approved-by <operator>`** — `service.arm_live_canary`: synchronous
   re-evaluation of the promotion gate, transition to LIVE_CANARY, fresh I2 epoch, live
   deployment and twin at one instant. *Expands real-money capability. Still places nothing.*
4. **the runtime allowlist** — `BOT_MODE=live`, `KILL_SWITCH=false`, `LIVE_ENABLED=true`,
   `LIVE_STRATEGIES=Cmmsell10`, plus the envelope's settings, through the `env` channel. This
   is the step at which an order can reach Kalshi, and it is a Live Ops act.

Steps 3 and 4 are deliberately separate switches. A script that could do both would be one
command away from unreviewed exposure.

## 10. Rollback and stand-down

- **Stop new entries:** `{"type":"env","set":{"LIVE_STRATEGIES":""}}` — or `KILL_SWITCH=true`
  for the portfolio. Held positions continue to exit and settle; they are still real money.
- **Undo the runtime config:** the envelope's settings are ordinary Railway variables and
  revert the same way.
- **The registration does not roll back, and should not.** A frozen version, a registered
  gate and a recorded transition are append-only by design. Retiring the canary is a recorded
  lifecycle move (`end_deployment` + a transition), never a deletion.
- **A retune mid-canary voids the comparison.** The fix is a new twin generation, not a
  re-read of the old one — and under Experiment OS a changed parameter is drift, which blocks
  the gate until it is classified.
