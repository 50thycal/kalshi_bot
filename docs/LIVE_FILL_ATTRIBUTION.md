# Live fill attribution — Platform Change Review record

**Role:** Platform Change Review. **Date:** 2026-10-16. **Workstream:** WS-027.
**Revision:** `METRICS_ENGINE:live_fill_attribution_2026_10` (not yet registered; see §7).
**Code:** `kalshi_bot/experiment_os/live_attribution.py`, switched in `metrics._live_market_rows`.

Experiment OS is canonical for every object named here. This document records the review: what
changes, what was measured, which experiments are affected and how, and the order in which the
change may go live. It authorizes nothing. Activation is an owner hard stop.

## 1. Standard output

```
PLATFORM CHANGE: METRICS_ENGINE pnl_scripts_2026_08 → live_fill_attribution_2026_10
                 (live provider: per MARKET, contested excluded → per FILL, per tag)
SEMANTIC EFFECT: every live_* and twin_* metric prices each tag from its OWN entry fills.
                 A market two tags traded is INCLUDED for both, split by fills, never pro rata.
                 Contracts are Kalshi's fractional count_fp, not the rounded integer column.
                 Provider revision recorded on each value: live_exec_v1 → live_fill_v1.
AFFECTED EXPERIMENTS: 15 active (6 LIVE_CANARY, 8 PAPER, 1 PAUSED) — table in §4.
                 Final list = affected_experiments() on the registered revision.
HISTORICAL RECOMPUTATION: EXACT. Every input (fills, live_orders, settlement snapshots) is
                 recorded; the provider recomputes any window from them.
CUTOVER PLAN: §7. Merge (inert) → REGISTER_REVISION → impact report → RECORD_IMPACTS →
              owner confirms activation → CUTOVER at a measured worker boot.
```

## 2. Why the shipped provider cannot serve shared markets

`positions` is keyed by market. `_live_market_rows` therefore drops any market another tag also
traded ("contested") from every live metric. That was a sound refusal while no two tags were
meant to share a market. The WIN-set canary (`docs/MMSELL_WIN_SET_CANARY.md`) is designed to
share them with `Kmmsell10`; under the shipped provider every shared market would vanish from
both books' gates — including the losing ones the keep gate exists to see.

## 3. What was measured before any code ran (production, read-only)

| read | finding |
|---|---|
| `ws027-fills-1` | The fills ⋈ `live_orders` join works for every mmsell book (XOS-000013 is a different, older defect). Kalshi reports a maker NO-buy as a fill with `side=no, action=sell` at the NO price, so **direction must come from the order**. On the incentive book the fill's side can be the complement of the order's, so **the price is mapped into the order's frame** (`100 − p`). |
| `ws027-recon-3` | **`fills.quantity` is rounded from `count_fp`.** `KXRAIN-26OCT13-MIN`: fills 1.50 + 0.75 + 0.75 stored as 2 + 1 + 1 = 4; Kalshi settled 3.00. The shipped provider's contract denominator sums the rounded column too — a pre-existing defect, fixed in the new provider only. |
| `ws027-recon-3` | The settlement snapshot carries `value` (YES settlement, cents). A `scalar` result (e.g. KXT20MATCH at 50) is priced from it; a yes/no-only rule would misprice it. |
| `ws027-recon-4` | With `count_fp` and `value`: **2,490 of 2,491** settled single-tag mmsell markets reconcile to `positions.realized_pnl` within 1¢ (one residual, $0.14), and **16 of 16** settled two-tag markets reconcile exactly in total. |
| `ws027-contested-1` | What the active canaries gain: size-split `Hmmsell10` and `Jmmsell10` +3 markets each (−$2.66; worst −$2.79, the XOS-000040 market, under the $3.00 bound); contest-cap `Fmmsell10` +3 (+$0.20) and `Emmsell10` +1 (+$0.06). Ceiling, capacity and cell-size gain none today. |

## 4. Affected experiments and dispositions (proposed)

| experiment | state | class / action | rationale |
|---|---|---|---|
| `liquidity-incentive-mm` | LIVE_CANARY | I1 / RECOMPUTE `metric:live_cents_per_contract` | live metrics recompute exactly from recorded fills; its book sells and holds both sides, which the fill formula prices (tested), and its reconciliation is listed by the impact report before activation |
| `mmsell-price-ceiling` | LIVE_CANARY | I1 / RECOMPUTE | exact; no contested markets today |
| `mmsell-price-ceiling-capacity` | LIVE_CANARY | I1 / RECOMPUTE | exact; no contested markets today |
| `mmsell-price-ceiling-cell-size` (`Kmmsell10`) | LIVE_CANARY | I1 / RECOMPUTE | exact; the `count_fp` fix moves `KXRAIN-26OCT13-MIN` from 4 to 3 contracts |
| `mmsell-price-ceiling-contest-cap` | LIVE_CANARY | I1 / RECOMPUTE | exact; +4 contested markets enter, all wins |
| `mmsell-price-ceiling-size-split` | LIVE_CANARY | I1 / RECOMPUTE | exact; +3 contested markets per tag enter, incl. the −$2.79 XOS-000040 market |
| `mmsell-correlation-cap`, `mmsell-rain-season`, `mmsell-reviewed-universe`, `mmsell-temperature-season`, `mmsell-type-tight`, `mmsell-weather-bands`, `mmsell-wide-control` | PAPER | I0 / NO_ACTION | paper deployments only; a live metric addressed at them is MISSING by construction, so no value they read changes |
| `theta-tail-sell` | PAUSED | I0 / NO_ACTION | no live deployment; nothing it reads changes |

**Why I1 and not I2.** Nothing about the world changed — the same orders, fills and settlements
— only how a recorded number is computed from them, and the recomputation is exact. I1 keeps
each epoch and requires a FRESH post-boundary evaluation before any result authorizes a
transition (`docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`). A pre-change PASS cannot promote.

**No verdict moves silently.** Which verdicts move is not asserted here from arithmetic; it is
measured. `xos attribution-impact` (`experiment_os/attribution_report.py`, read-only) evaluates every live
gate twice as a dry run — shipped provider, then fill attribution forced on in its own process —
and prints every verdict and clause that differs, then the reconciliation of every settled
market. It runs after merge, before `RECORD_IMPACTS`, and its output goes into this table. A
moved verdict on a LIVE_CANARY changes the disposition discussion with the owner, never silently.

## 5. Reconciliation is durable, and non-blocking

On the designated writer, once the revision is active, each evaluation cycle reconciles markets
settled in the last 7 days: Σ per-tag fill P&L against the account's `realized_pnl`, tolerance
$0.02. A mismatch — or a fill no order explains — records one `LIVE_ATTRIBUTION_MISMATCH`
integrity event per (experiment, market), never twice while unresolved, routed to Platform
Change Review. It is **non-blocking** for the same reason `EXPERIMENT_CONFIG_UNVERIFIABLE` is:
it is a claim about one market's attribution, the metric keeps the tag's own fill figure, and
blocking would hold the KILL gate — the dangerous direction. Loud, never silent.

## 6. Does admitting a sharing partner change `Kmmsell10`'s world? — No (ruling)

Asked explicitly by the WS-027 handoff (§2e). Three channels by which `Nmmsell10` could reach
`Kmmsell10`'s evidence, each examined:

1. **Admissible markets.** Unchanged. Without the pair, an armed `Nmmsell10` would *shrink*
   Kmmsell10's universe — `gate:held` and the in-flight check would refuse Kmmsell10 on every
   market Nmmsell10 entered first. The pair exception is what keeps Kmmsell10's admissible set
   identical to a world without Nmmsell10. The combined bound (6 = 3 + 3) always fits
   Kmmsell10's largest clip beside Nmmsell10's.
2. **Queue position.** Books are visited per market in `MMSELL_VARIANTS` order and the live
   mirror fires inside that visit (`MmSellTracker`), so with `Nmmsell10` appended after
   `Kmmsell10`, Kmmsell10 rests first on any market both want in the same cycle and keeps
   price-time priority. Nmmsell10 bears the queue cost. The order is recorded in the envelope
   (`shared_market.queue_priority`).
3. **Shared account breakers.** `MAX_TOTAL_EXPOSURE` and `MAX_DAILY_LOSS` are account-wide.
   Neither refused a single live entry in the 14 days to 2026-10-16 (`ws027-gates-1`), and
   Nmmsell10's expected footprint is ~$28. Re-read at the arming preflight.

**Ruling:** no new epoch, no new Version for `Kmmsell10`. **Pre-registered tripwire** (this is
what would re-open the ruling, and it authorizes nothing by itself): Kmmsell10's contract fill
rate on the seven WIN series was **64%** (41/64, 25 markets, 2026-10-11 → 16, `ws027-kfill-1`).
If its fill rate on markets SHARED with Nmmsell10 falls below **44%** at ≥ 60 resolved shared
contracts (per-tag `live_fill_rate_pct` provenance → `shared_markets`), Platform Change Review
re-rules and a new epoch for Kmmsell10 is the expected remedy — never pooling across it.

## 7. Cutover plan — exact order

| # | step | who | tier |
|---|---|---|---|
| 1 | Merge the WS-027 PR. Inert: no revision is active, every gate computes byte-identically, the pair list is empty. | owner | hard stop (touches a live safeguard) |
| 2 | `REGISTER_REVISION` `{"component":"METRICS_ENGINE","version":"live_fill_attribution_2026_10","fingerprint":<metrics_fingerprint() of the merged file>,"pr_ref":"<PR>"}` — pending, activates nothing | PCR | request-authorized |
| 3 | `{"type":"xos","command":"attribution-impact"}` — every live gate before/after, reconciliation. Record the result in §4. | PCR | free (read) |
| 4 | `RECORD_IMPACTS` — the 15 rows of §4 (I1 rows carry `normalization_ref`), as one batch | PCR | request-authorized |
| 5 | **Owner confirms activation**, in words naming the revision | owner | **hard stop** |
| 6 | `CUTOVER` `{"revision":"METRICS_ENGINE:live_fill_attribution_2026_10","expect_metrics_fingerprint":<same>,"apply_accepted_recompute":true}` — activates at the measured worker boot, applies the accepted I1s at that instant; defers on a worker not serving the reviewed file | PCR | (authorized by 5) |
| 7 | Readback: `xos platform`, `control-tower` (no new BLOCKED_PLATFORM), receipt in logs | PCR | free |

`fingerprint` is printed by the impact script (`expect_metrics_fingerprint` line). It hashes
`live_attribution.py` only, so an unrelated `metrics.py` edit does not strand the cutover.

## 8. Two transport gaps closed on the way (narrow, opt-in)

Found while planning step 4–6, and true of every revision, not just this one:

- **`RECORD_IMPACTS` could not carry a `normalization_ref`**, and the engine refuses an I1
  without one — so an I1/RECOMPUTE was unrecordable in production. Rows now accept an optional
  `normalization_ref`, validated by the engine against the registry, never trusted.
- **Nothing in production could APPLY an accepted RECOMPUTE**, and only an applied one licenses
  the pinned-vs-active difference. Activating a revision with I1 dispositions would have left
  each such experiment `BLOCKED_PLATFORM`. `CUTOVER` accepts `apply_accepted_recompute: true`,
  applying them through the engine's own `apply_recompute` at the measured instant. Default off:
  the two other pending revisions' cutovers are unchanged. (An accepted NO_ACTION already
  settles as applied at acceptance.)

Plus `expect_metrics_fingerprint`, the METRICS_ENGINE analogue of the execution-engine proof.
None of the three can change exposure, place an order, transition a lifecycle state, record a
verdict, fabricate a Version or force activation.

## 9. What this does not change

No gate contract, threshold, Version, epoch or exposure. No live safeguard. The shipped
provider is untouched and remains what runs until step 6. Paper metrics are untouched.
