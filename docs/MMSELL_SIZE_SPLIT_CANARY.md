# mmsell size-split live canary (`Hmmsell10`) — the pre-registered plan

Written 2026-10-03, **before** registration or arming, so nothing here can be chosen after a
result. Operator direction (Calvin, 2026-10-03): MMSELL stays a profit line and position size is
its lever to the $100/month north star (`DEC-025`); "start prepping for a new live canary and
shutting down the old". Experiment OS is canonical for every standing this document describes;
where they disagree, Experiment OS wins.

Code: `kalshi_bot/experiment_os/successor_mmsell10_size_split.py` (package
`mmsell-size-split-canary`), `live/sizing.py::ticker_size`, the `sizes=` book key
(`config.py`), `MmSellTracker._book_contracts`. Tests: `tests/test_successor_mmsell10_size_split.py`,
`tests/test_mmsell_size_split.py`. Evidence: `docs/MMSELL_REPLAY_PROBES_20261003.md`.

## 1. What changes against `Fmmsell10`

| | `Fmmsell10` (stands down) | `Hmmsell10` (successor) |
|---|---|---|
| book spec | `lo=5,hi=10,maxyes=7,size=1,contestcap=1` | `lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,sizes=1+3` |
| contracts per order | 1 | **1 or 3, fixed per ticker by hash** (~50/50) |
| contest key | shipped (a KXRAIN day = one contest) | **split** (each KXRAIN city / mention word = its own contest) |
| per-order / per-market cap | $1.00 | **$3.00** (one 3-lot) |
| canary loss budget | −$15 | **−$30** |
| keep-gate per-market loss bound | $1.00 | **$3.00** (one 3-lot clip) |
| everything else | band, ceiling, contest cap 1, rung cap 3, 0¢ offset, 4 h timeout, hold to settlement, 40-open cap, twin cap 250, tier bar, fee model, promotion gate | **unchanged** (asserted in CI) |

### Which markets get the larger size

**A random half of all markets, chosen by ticker hash — not by market type.** Every ticker that
passes the book's normal gates is sized `sha256(salt + ":sizes:" + ticker) % 2` → 1 or 3
contracts, and keeps that size for its whole life (retries cannot flip it). Nothing about the
market decides it, deliberately: that makes the 1-contract half a concurrent control for the
3-contract half, in the same regime, universe and scan, so what the extra contracts do is
measured rather than confounded with what kind of market they were in.

Market-based sizing (an "S tier") comes later and from evidence: the slow-information cell
(weather, mentions, scheduled discrete markets) survived an independent retro window on
2026-10-03 and its forward read is accruing. If it passes, the next successor sizes that cell up
on purpose. Not before.

### Why one book, not two

Two books (`part=0/2,size=1` / `part=1/2,size=3`) would double the contest cap (two positions
per game), need two twins, and leave the account-wide exposure and daily-loss breakers deciding
which book scans first (`docs/MMSELL_BOOK_PARTITION.md`, "What this does NOT fix"). One book
with a per-ticker count keeps one of each.

## 2. The risk envelope (registered on the version as `risk_json`)

| limit | value |
|---|---|
| contracts per order | 1 or 3 (per-ticker hash) |
| max order / per-market exposure | $3.00 / $3.00 |
| positions per contest | 1 (`contestcap=1`, split key) |
| rungs per event ticker | 3 (unchanged; binds a KXRAIN day at 3 cities) |
| per-event exposure | $9.00 |
| open positions | 40 |
| book exposure | expected ~$75 at the 50/50 mix; worst case $116.40 (all 3-lots) |
| canary loss budget | −$30 (keep gate) |
| order timeout | 4 h, then cancel |
| exit | hold to settlement |

### Global settings the cutover writes — named (live-paper-parallel §3b)

| variable | now | at cutover | who else reads it |
|---|---|---|---|
| `LIVE_MAX_ORDER_DOLLARS` | 1.0 | **3.0** | only the mmsell live mirror (theta and the incentive book use their own knobs; weather is not live). Every other mmsell book declares `size=1` or is not in `LIVE_STRATEGIES` |
| `MAX_TOTAL_EXPOSURE` | 100 | **125** | portfolio-wide, shared with `Alimm1` (~$40). Recommendation, not a requirement: at 100 the breaker would start refusing this book's entries once `Alimm1` + the draining `Fmmsell10` positions + ~$55 of new book are held |
| `MAX_DAILY_LOSS` | 50 | unchanged | shared; raised 5 → 25 → 50 by operator decisions for the incentive book |
| `MAX_MARKET_EXPOSURE` | 25 | unchanged | shared with `Alimm1` |
| `LIVE_PAPER_TWIN_SUFFIX` | `_pt4` | unchanged | global; the twin tag is derived from it |

**Observation for Live Ops (not acted on here):** the predecessor's registered envelope declares
`MAX_MARKET_EXPOSURE=1.0` and `MAX_DAILY_LOSS=5.0`; production holds 25 and 50. The per-order cap
and `size=1` kept `Fmmsell10` inside its $1 market bound regardless, but the registered settings
and the running ones disagree.

## 3. Sequence — what is authorized, and the hard stops

1. **Merge the PR** — *hard stop*: the diff touches live sizing and the arming path.
2. **`REGISTER_PACKAGE mmsell-size-split-canary`** (`xos package-preflight` first). Ends only the
   predecessor's PAPER deployment (hands `mmsell10` over); arms nothing.
3. **Wait** for `mmsell10` to settle paper trades inside the successor's window (about a day).
   `arm_live_canary` re-evaluates the promotion gate synchronously and refuses until it can.
4. **Cutover** — *hard stop*, ONE env request so the arm and the allowlist land on one boot:
   - `EXPERIMENT_OS_EXPERIMENT_COMMAND` = one `ARM_CANARY` envelope, package
     `mmsell-size-split-canary`, `actor_role: LIVE_OPS`, `approved_by` the operator;
   - `MMSELL_VARIANTS` = the **live value read at cutover** with
     `;Hmmsell10:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,sizes=1+3` appended. Never
     hand-composed. `Fmmsell10`'s entry **stays** (as `Cmmsell10`/`Dmmsell10` did);
   - `LIVE_STRATEGIES` = the live value with `Fmmsell10` **replaced** by `Hmmsell10`
     (today: `Hmmsell10,Alimm1`). This line is the shutdown of the old canary;
   - `LIVE_MAX_ORDER_DOLLARS=3.0`, `MAX_TOTAL_EXPOSURE=125`.
5. **Verify**, in order, stopping at the first red: receipt SUCCEEDED; `live_paper_twins` holds
   an **open** row for `Hmmsell10_pt4`; first `live_orders` for `Hmmsell10` within two scan
   cycles, with quantities 1 and 3 matching `ticker_size`; no new `Fmmsell10` orders; control
   tower reports `mmsell-price-ceiling-contest-cap` as EXPERIMENT_EXECUTION_STOOD_DOWN (not
   drift); `Fmmsell10`'s open positions keep settling with evidence recorded.

**What the shutdown of `Fmmsell10` does and does not do.** It stops new entries. Its ~23 open
positions are real money and settle over the following days; its live and twin deployments stay
open so every settlement is recorded. Nothing is sold early.

## 4. The keep/stop contract

The predecessor's `live_canary_keep` with exactly two thresholds restated (CI asserts the rest is
equal): `live_realized_pnl_usd <= −30` (was −15) and `live_max_realized_loss_usd > 3.0` (was
1.0 — at 1.0 the first 3-lot loss would read as "envelope not applied"). Unchanged: the
150-contract sample floor and 600 horizon, the ±0.5¢ twin paired-gap stops, the 5 pp win-rate
stop, the coverage and fill-rate holds.

## 5. The readout — pre-registered now, read on fills from the arming instant

Arms are recomputed from the ticker (`ticker_size`, salt `mmsell-partition-v1`). Outcome per
filled order: `(settle_NO − limit) × filled contracts`; per-contract figures divide by filled
contracts. Bootstrap: settlement-date blocks, 5,000 resamples, seed 20261003.

| id | claim | PASS | FAIL |
|---|---|---|---|
| S0 instrument | the split is the one that ran | ≥ 98% of placed orders carry the hash arm's quantity | < 98% → HOLD (instrument) |
| S1 fill completeness | the extra contracts fill | ≥ 70% of 3-lot orders with any fill end fully filled | < 70% → the clip is too large for the flow |
| S2 marginal economics | the extra contracts are not adversely selected | per-contract realized (3-lot arm) − (1-lot arm) ≥ −1.0¢ and bootstrap 5th pct > −3.0¢, at ≥ 150 settled markets per arm | difference ≤ −3.0¢ at the floor |
| S3 risk | the envelope holds at size | no keep-gate stop, no envelope breach | any stop |

**Next step, decided 2026-10-04 (`DEC-026`):** after about a week of this split, size by win rate against entry price — larger positions in cells that win more often than their price implies (first candidate: the slow-information cell), small elsewhere. The one-week review reports S0, S1, S3 and the slow cell's forward count as they stand; S2 will be underpowered at a week and is reported as such.

S2 is a **non-inferiority guard**, not a precision estimate: at ~25¢ per-contract dispersion,
150 markets per arm resolve a difference of roughly ±3¢ — enough to catch the extra contracts
being badly picked off, not to rank small effects. **Decision:** S0–S3 PASS → a
`sizes=3+5` (or `1+5`) successor is the next step, a new package and a hard stop. S2 FAIL → back
to one contract and the size lever closes on this flow.

Reported alongside, never decisive: the contest-key correction — fills per day and realized ¢ on
`regimes.SUBJECT_SPLIT_SERIES` against `Fmmsell10`'s baseline (24 fills in 24 days, +6.75¢);
dollars per month at the realized mix; and the 2026-10-03 forward reads (slow-information cell,
decision-time spread), which continue on `Hmmsell10` as the registered successor of the same
book.

**Honest expectation.** At today's pace (~29 fills/day, +0.73¢/contract) the 50/50 split is about
58 contracts/day ≈ $13/month, plus perhaps 2 fills/day from the key correction ≈ $5–6/month:
roughly **$15–20/month** if the per-contract edge holds. The canary's job is to show the size
step is safe and linear; the next steps (5-lots, a sized-up slow-information cell) are where the
book's share of $100/month comes from.

## 5b. Epoch 3 — `Jmmsell10` (2026-10-04, `DEC-027`)

`Hmmsell10` lost live-tradable entries to paper-only positions in live-paused series filling a
contest slot (XOS-000038). The fix (PR #531, `MMSELL_LIVE_CAPS_COUNT_LIVE_ELIGIBLE_ONLY`) changes
the live candidate population, so it goes on with a new epoch on fresh tags — package
`mmsell-sizesplit-epoch3` (`kalshi_bot/experiment_os/recut_mmsell10_size_split.py`). Same contract,
envelope and keep gate. The §5 readout counts from the epoch-3 boundary; `Hmmsell10`'s evidence is
historical only and does not pool.

## 5c. Tennis paused on real money (2026-10-09, operator decision)

Experiment OS issue: **XOS-000039** (owner Research Lab, for the lift/keep read). The
cross-tag stacking found in the same review is **XOS-000040** (Live Ops).

Calvin, 2026-10-09: "bar tennis — it has never gone well for me." Implemented as the existing
live-only exposure pause (`mmsell_live_skip_series`), adding the prefixes `KXATP`, `KXWTA`,
`KXITF` beside `KXNFLSPREAD`. Every tennis series is covered (ATP/WTA/ITF match winners,
Challengers, set winners, exact match, game spread/total, doubles).

**Evidence at the decision** (read-only Live Ops read, ops `jcanary-truth-1009` /
`jcanary-fills-1009`, 2026-10-09 12:03Z). `Jmmsell10` real money −$12.98 after five days
(−$13.39 realized over 162 settled, of which about −$2.79 belongs to `Hmmsell10`; see the
cross-tag stacking issue). With MLB over, tennis was ~43% of settled markets. ITF singles
(`KXITFWMATCH` + `KXITFMATCH`) lost 6 of 45 for −$7.97. The losses were mostly in-play fills
within 1–3 min of posting that settled against the book within two hours. This is in-sample and
small, so it is an **exposure pause, not a measured selection rule**, the same standing as
`KXNFLSPREAD`.

**What it changes, and what it does not**

- Live `Jmmsell10` stops taking new tennis entries. Open tennis positions settle as usual.
- **The twin stops too**, because production runs `MMSELL_TWIN_APPLIES_LIVE_BARS=true`. Live
  and twin stay on one universe, so the keep gate's twin comparisons stay like for like.
- Paper `mmsell10` keeps trading tennis. That is the evidence that would lift the pause.
- The pause applies to both hash arms equally, so the §5 1-vs-3 readout stays a within-book
  randomized comparison. Its universe is narrower from the activation instant. The readout
  reports the pre/post split and does not hide it.
- Nothing about the version, the risk envelope or the keep gate changes. The bar can only
  refuse an entry.
- **Not fingerprinted by the drift check.** `runtime_config_check` compares
  `LIVE_STRATEGIES`, twin pairs and `MMSELL_VARIANTS` only, so this edit raises no integrity
  event. That is exactly why it is recorded here and in an Experiment OS issue instead.

**Lift path:** remove the three prefixes. Re-read paper tennis after a full month of fills
before anyone proposes that.

## 5d. BTC daily price paused on real money (2026-10-10, operator decision)

Experiment OS issue: **XOS-000041** (owner Research Lab, for the lift/keep read).

Calvin, 2026-10-10, after a top-losers read of the three canary tags (ops
`canary-toplosers-1010`): "remove the BTC daily price too". Same instrument as §5c:
`KXBTCD` is added to `mmsell_live_skip_series`. Live and twin stop taking new BTC daily
entries, paper `mmsell10` keeps trading them, and open positions settle as usual.

**Evidence at the decision.** `KXBTCD` was the canaries' second-worst series: −$5.87 over 27
markets, 4 losers. Most of that is one market that `Hmmsell10` and `Jmmsell10` stacked 3+3
contracts on (−$5.58, XOS-000040). The other 26 net about −$0.29. So this bar is mostly a
response to one stacked loss, plus the BTC daily book's general tail risk at 3 contracts. It
is not evidence that BTC daily loses on its own.

**Scope.** Exactly `KXBTCD`. The hourly range series `KXBTC` and the monthly `KXBTCMAXMON`
are not covered; widening it is a separate operator call.

**Lift path:** remove `KXBTCD`, after a paper re-read.

## 6. Rollback

- **Stop new entries:** remove `Hmmsell10` from `LIVE_STRATEGIES` (or `KILL_SWITCH=true` for the
  portfolio). Held positions still settle.
- **Undo the runtime:** remove the one `Hmmsell10:` entry from `MMSELL_VARIANTS` (never clear the
  variable); `LIVE_MAX_ORDER_DOLLARS` back to 1.0 and `MAX_TOTAL_EXPOSURE` back to 100 once
  nothing larger is held.
- The registration does not roll back: frozen versions and recorded transitions are append-only.
