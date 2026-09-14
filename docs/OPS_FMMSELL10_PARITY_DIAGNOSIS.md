# Live Ops diagnosis — why `Fmmsell10` and its twin disagree

**Session:** Live Ops, 2026-09-13. **Status:** operational diagnosis. **No production
lifecycle state, gate, exposure or config was changed.** Every figure is read from
production through the ops channel and carries the request id behind it.

Experiment OS is canonical for this book's lifecycle, standing and gate verdicts. This
document restates none of them. It answers one operational question: **the parity report
calls `Fmmsell10_pt4` an `EXECUTION GAP` — is that the right diagnosis?**

It is not. The gap is a **universe-composition asymmetry** between the live book and its
twin, introduced by the two live-only bars added on 2026-09-05/09-06, which the parity
report structurally cannot see.

---

## 1. The read

Epoch `Fmmsell10` / `Fmmsell10_pt4`, started `2026-09-07T02:03:36Z`, running, age ~153h.

| | value | source |
|---|---|---|
| live realized | **+$1.3140** over 229 settled | `lo-truth-913a` |
| live unrealized | −$0.7150 on 13 open | `lo-truth-913a` |
| **live TOTAL** | **+$0.5990** | `lo-truth-913a` |
| twin paper | +$14.5515 over 462 settled | `lo-parity-913a` |
| incumbent paper, live tag | +$11.0399 over 462 | `lo-truth-913a` |
| fill rate | 241/345 = 69.9% (312 tickers ordered, 242 filled = 77.6%) | `lo-parity-913a`, `lo-truth-913a` |
| `px_gap` | **+0.09c** | `lo-parity-913a` |
| matched markets, n=227 | twin +0.81c/ct vs live **+0.95c/ct**, gap **−0.14c** | `lo-parity-913a` |

The matched-market row is the load-bearing one: on 227 markets both sides settled, the
simulator and real money agree to within 0.14c/contract, and `px_gap` is 0.09c. **The
accounting is sound and the cost basis assumption is right.** Nothing here indicts the
paper engine.

## 2. Where the $11.19 of phantom paper P&L actually went

`live_book_truth` splits the 462 paper trades under the live tag by what real money did:

| bucket | n | paper P&L | share of phantom |
|---|---|---|---|
| live **filled** it | 229 | −$0.1525 | — (real money made **+$1.3140** on these) |
| ordered, **never filled** | 66 | +$4.3296 | **39%** |
| **never ordered** (a live bar refused it) | 167 | +$6.8628 | **61%** |
| refused at the **open cap** | **0** | **$0.00** | **0%** |

**On every market live actually traded, live beat its own paper model** — the incumbent
paper book models those 229 markets at −$0.15; real money realized +$1.31. There is no
execution deficit on captured trades at all.

## 3. Which bar refused the 167 — and it is not the cap

The parity report's §2 table shows those candidates as `not_attempted`, which reads like
"live never got to it". `not_attempted` is not a gate: it is the **default** the twin
harness writes when the executor recorded no decision (`twin/harness.py:286`). The two
live-only bars in `mmsell/tracker.py` refuse the mirror *before* the executor is called
and record to `parent_outcome`, so `live_outcome` stays at that default.

Cross-tabbing `parent_outcome` against `live_outcome` for every candidate the twin opened
(`lo-nat-913a`):

| parent did | live did | n | distinct series |
|---|---|---|---|
| opened | placed | 304 | 65 |
| **`skip_live_tier`** | `not_attempted` | **177** | **75** |
| `skip_live_paused` | `not_attempted` | 15 | 1 |
| `skip_already_open` | `gate:dedup` | 5 | 5 |
| `skip_contest_cap` | `not_attempted` | 2 | 2 |
| opened | rejected | 1 | 1 |
| **`gate:open_cap`** | — | **0** | — |

**`MMSELL_LIVE_MAX_OPEN_POSITIONS=40` did not refuse a single candidate this epoch.** The
book holds 12–13 open against that 40. The cap is not the binding constraint, and it
cannot be: the tier bar removes 91% of the missed flow upstream, before the executor —
and therefore before the cap — is ever consulted.

The binding constraint is `mmsell_live_min_tier` (default `graduated`, added 2026-09-05),
with `mmsell_live_skip_series=KXNFLSPREAD` (added 2026-09-06) accounting for the other 15.

## 4. The fidelity defect this exposes

`docs/LIVE_PAPER_TWIN.md` and the `live-paper-parallel` skill both state the twin's
invariant: *the only remaining difference is that the twin assumes its resting order
fills.* **That invariant has been broken since 2026-09-05.**

In `mmsell/tracker.py` the twin branch (`if is_twin:`) returns before reaching
`_live_tier_blocks` / `_live_paused_blocks`, so those bars apply to the live mirror only.
The twin therefore trades a universe of 75 series the live book is forbidden to touch.
Both sides are behaving as coded; the *comparison* is what is invalid.

Consequences:

1. **The `EXECUTION GAP` verdict on `Fmmsell10_pt4` is a misdiagnosis**, and the same
   misdiagnosis will be produced for any future book carrying a live-only bar. The
   verdict logic reads `twin_outcome` vs `live_outcome` and never consults
   `parent_outcome`, so a universe restriction is indistinguishable from an execution
   failure in the report's own output.
2. **This book's headline paper edge is not measured on the universe it can trade.** The
   twin's +3.15c/ct is carried by markets live may not enter. On the 65 series live can
   reach, both sides read ~+0.9c/ct.
3. The `mmsell10-queue-aware-cancel` package is premised on a baseline of "67–145
   candidates a day refused at `gate:open_cap`" (`queue_aware_cancel.py`). **That premise
   does not hold for `Fmmsell10`** — zero such refusals this epoch. The premise should be
   re-measured on the current book before the package is registered or run.
   (`LIVE_QUEUE_CANCEL_MODE=off` today, so nothing is running on the stale baseline.)

## 5. What the 66 never-filled orders say — and this part *is* execution

The 66 ordered-but-unfilled markets model at +$4.3296, i.e. **+6.56c/contract on a book
whose winner pays ~+7c**. Essentially all of them would have won. Against that, the 229
markets live *did* fill ran a 93.0% win rate.

Orders fill when the market is about to move against us and rest unfilled when it drifts
our way. That is ordinary maker adverse selection, it is structural, and **no cap change
or bar change recovers it** — it is the cost the twin was built to price. It is 39% of
the gap and it is real.

## 6. Boundary — what this diagnosis does NOT conclude

Whether the 167 tier-barred trades represent a genuine edge is a **scientific** question
and is not settled here. The operational facts, for whoever picks it up:

- The barred set settled ~167 markets for +$6.86 with ~4 losing tails (~2.4%).
- The twin's live-reachable set ran ~4.7% losing tails over the same window.
- At a ~93c cost basis paying ~+7c, breakeven is a **~7% loss rate**; live's realized
  loss rate on captured trades is **7.0%**.
- The difference between 2.4% and 4.7% over 167 markets in one 6-day window is **not
  statistically distinguishable** (Poisson, expected ~7.9 losers, observed ~4, p≈0.10).
- One hypothesis was tested and **rejected**: the barred flow is *not* a stacked spread
  ladder. `contestcap=1` held — the NCAAF block is 63 distinct contests at exactly one
  rung each (`lo-ncaaf-913a`), so this is not the `KXNFLSPREAD` independence-unit error
  of XOS-000022.

So the barred population looks better than the reachable one, on a sample far too thin
and too regime-specific (opening weeks of the college football season) to act on. That
question belongs to **Research Lab**, on its own ticket, with a pre-registered bar.

## 7. Open items for the owning roles

| item | owner |
|---|---|
| Twin does not apply the live-only bars; the documented twin invariant is false since 2026-09-05. Fix is a re-scoped twin on a **new twin tag**, not a re-read of this one. | Live Ops + Platform Change Review (shared semantic) |
| `live_paper_parity` verdict logic ignores `parent_outcome` and mislabels a universe restriction as `EXECUTION GAP`. | Platform Change Review |
| Is the tier-barred universe genuinely +EV, or a thin early-season sample? | Research Lab |
| `queue_aware_cancel` baseline (`gate:open_cap` 67–145/day) is stale for the current book. | Research Lab |
| `PARAM DRIFT` is logged against `Fmmsell10_pt4`; no drift line appears in current boots, so it is historical and unattributed. Formally the one-to-one property is already impaired for this epoch. | Live Ops |

## 8. Real-money note

Largest open position `KXRT-RUN-65`, entered at 93c, now bid 1c — an unrealized −$0.92,
which is by itself larger than the book's entire realized P&L. Within the pre-registered
$1.00 per-market envelope and the $15.00 canary loss budget; recorded, not escalated.

Realized run rate is +$1.31 over 6.4 days ≈ **$0.20/day ≈ $6/month** against the
$100/month north star. At `size=1` and `LIVE_MAX_ORDER_DOLLARS=1.0` this book cannot
approach that figure whatever the bars do; it is a canary measuring whether the edge
survives contact with real money, and on that question it is currently reading **yes, but
small, and only on the universe it is allowed to trade**.

---

## 9. The fix, and how to activate it (added 2026-09-13, Live Ops)

§4's defect is now addressed in code, **switched off**. Merging changes nothing that runs.

`MMSELL_TWIN_APPLIES_LIVE_BARS` (default `false`) applies the two live-only bars —
`mmsell_live_min_tier` and `mmsell_live_skip_series` — to the **twin** as well as to the live
mirror. The incumbent paper book is deliberately untouched: paper is still how a series
accumulates the history that graduates it. Only the twin moves, because the twin is not paper —
it is live's mirror, and sharing live's universe is what makes it one.

The bars are checked directly in the twin branch rather than through `_live_paused_blocks` /
`_live_tier_blocks`. Those two ask `_live_would_act`, which rejects paper-twin tags, so routing
the twin through them would refuse the entry **without recording which bar did it** — the exact
blindness this fixes. Twin refusals land in their own counters (`twin_skipped_live_tier`,
`twin_skipped_live_paused`) so a simulated refusal can never be read as a real-money entry the
bar saved.

### Why it defaults off, and what activation requires

Turning this on changes what an already-running twin trades, and retuning a live comparison
mid-epoch normally voids it.

> [!CAUTION]
> **An earlier revision of this section told you to set `LIVE_PAPER_TWIN_SUFFIX` to a fresh tag
> alongside the flag. That was WRONG and it would have taken the twin DARK.** Verified against
> production 2026-09-13 (`lo-pt5-913a`): `xos tag Fmmsell10_pt5` answers *"not mapped to any
> experiment deployment"*. Under `NEW_ONLY` an unregistered tag is refused at the write path, so
> a suffix bump does not start a new twin — it stops the existing one and replaces it with a tag
> that cannot trade. Do not do it.

**A genuinely fresh twin tag is not available for this book, and that is the system working.**
Three independent reasons, each checked rather than assumed:

1. A new tag must be registered to an active deployment arm before it can write.
   `register_deployment(kind="paper_twin")` is *not* mode-guarded (only `kind="live"` is), so
   registering one is technically permitted —
2. — but readiness check 6, `live_twin_links`, requires a **native** canary's twin to share its
   live deployment's `started_at`. `mmsell-contestcap-live-2` is native (`grandfathered: false`,
   `lo-enf-913a`), and a twin registered today cannot match a 2026-09-07 live start. Stamping
   the old start on it would be *inventing an equal boundary* — precisely what
   `enforcement.py` refuses to do. It would also leave two twins on one live deployment, which
   that check reads with `session.scalar`, i.e. it would pick one arbitrarily.
3. The sanctioned way to get fresh tags — `arm_live_canary` — requires `PAPER`, and
   `LIVE_CANARY → PAPER` is an illegal rollback. That is the successor-experiment path
   (`successor_mmsell10_capacity.py`), which re-arms **real money** and is an operator hard stop.

### The activation: ONE variable, keeping `_pt4`

```jsonc
{"type":"env","action":"set",
 "values":{"MMSELL_TWIN_APPLIES_LIVE_BARS":"true"},
 "id":"twin-bars-on-1"}
```

Do **not** touch `LIVE_PAPER_TWIN_SUFFIX`.

Re-scoping the running `_pt4` mid-epoch costs nothing real here: its comparison is *already*
invalid (that is this entire document) and it already carries `PARAM DRIFT`. Flipping the flag
stops producing bad data rather than destroying good data.

The one consequence is that `_pt4` then carries a **mixed window** — wide-universe rows before
the flip, live-scoped rows after. Read it with a date floor rather than whole-epoch, which the
script supports directly:

```jsonc
{"type":"script","name":"live_paper_parity","args":["--twin","Fmmsell10_pt4","--days","7"],
 "id":"parity-postflip-1"}
```

Record the flip instant when you send it; everything before it is the old regime. The first
meaningful post-flip read needs n≥30 settled per side, so roughly a week.

Setting env **redeploys the worker**. Resting orders live on the exchange and survive it;
reconciliation runs on boot.

### No Platform Revision is required

Checked, not assumed. The flag changes none of the ten `STANDARD_PLATFORM_COMPONENTS`
(`FEE_MODEL`, `FILL_MODEL`, `MARKET_TAXONOMY`, …) — it changes which universe one instrument
book consults, which is deployment config. The governing precedent is XOS-000035 (Platform
Change Review, 2026-09-12): a deployment-config change is `OPS_REPAIR` with
`requires_platform_revision = false`, and "a ticket arriving at this role does not become a
revision by arriving; the evidence has to call for one."

### What this fix does NOT do

It repairs the **instrument**, not the book. Measured on the current epoch, restricting the twin
to live's own universe moves paper from +3.24¢/contract to **+2.37¢**, against live's realised
**+0.65¢** (`lo-restrict-913a`, `lo-truth-913b`). So the universe mismatch is ~34% of the gap;
the remaining ~66% is fill selection — the resting orders that never fill are the winners, and
no bar or cap recovers that. Expect the re-scoped twin to be *honest*, not *flattering*.

