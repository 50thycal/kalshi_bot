# LIMM nomination ladders: do the new election ladders pay net of fills?

**Status:** PRE-REGISTERED 2026-11-14 (Research Lab), before a single snapshot of these
markets exists. Owner lock: 2026-11-14 ("do one and two" on
[`IDEA_MODEL_20261114_BRAINSTORM.md`](IDEA_MODEL_20261114_BRAINSTORM.md), idea #1 there).
**Read once on 2026-12-08** (see §6). Experiment OS is canonical. The study is read-only and
descriptive: it nominates, and any universe or live change is an owner decision.

## 1. Question

Study 5 (`LIMM_FILL_HAZARD.md` §8) found only two series that paid clearly after fills:
`KXGENERALCOUNTY` (+$2.64 per market-day) and `KXSTATEBALLOTMEASURE` (+$2.12), both 2026-11-03
election ladders. The next cycle's nomination and race ladders now hold about 27% of the live
incentive pool, and the collector watches none of them (ops `idea-lims-1114c`: 0 of 1,551).

> Is "slow election ladder" a family that pays a cheap-side bid net of the fills it takes, or
> were study 5's two winners a one-off?

## 2. Instrument (the only build)

The shadow collector gains **watch-only research pins** (`collector.RESEARCH_WATCH_*`):

- **Series (frozen):** `KXSENATENOM`, `KXGOVNOM`, `KXDPRESPRIMARY`, `KXRPRESPRIMARY`,
  `KXAGRACE`, `KXTREASRACE`, `KXSOSRACE`, `KXAUDITORRACE` (the board survey of 2026-11-14).
- **Markets:** the top **10 per series** by daily pay, so at most 80, re-ranked at each
  discovery, beyond the 150-market cap.
- **Watch-only:** a research pin that the collector would not otherwise track is snapshotted and
  its prints recorded, but it is **never an arm candidate**. S1–S4 (`LIMM_PAPER_STRATEGIES.md`)
  select from exactly the markets they would have seen without this change, so their 11-24 read
  is untouched. A test pins this.
- **Expiry:** the pins switch off on their own at **2026-12-22T16:00Z** (after the only
  possible re-read).

## 3. Data and window (no lookahead)

- **Window:** 2026-11-16T00:00Z → **2026-12-08T15:00Z**. Fixed now. The pins deploy with this
  PR's merge; any earlier hours simply hold no rows.
- **Probe:** `scripts/limm_fill_hazard.py` **unchanged** (allowlisted), run with
  `--since 2026-11-16T00:00:00+00:00 --until 2026-12-08T15:00:00+00:00`. Hours, cheap side,
  size, reward estimate, the `conservative` fill model (deciding), the `optimistic`
  sensitivity, the 6 h markout and the event-clustered standard error are study 5's §2–§3.
- **Rows read:** only the eight series in §2. Every other row the script prints is ignored for
  this verdict.

## 4. Labels (study 5's, unchanged)

Per series floor **≥ 20 market-days and ≥ 5 events**, else `THIN`. `NET-LEAD`: `R` ≥ $0.50 per
market-day and `R` − 1.96·se > 0. `FILL-TRAP`: reward ≥ $0.50 per market-day and `R` ≤ 0.
`NO-LEAD`: every other series at the floor. `*` marks a label that differs under `optimistic`.

**Pooled family `R`:** Σ(net per market-day × market-days) ÷ Σ market-days over the series at
the floor, computed from the printed columns.

## 5. Verdicts

| verdict | rule | meaning |
|---|---|---|
| `HOLD (accrual)` | fewer than 3 of the eight series at the floor | one re-read on 2026-12-22, window end 2026-12-22T15:00Z, same bars |
| `FAMILY-LEAD` | ≥ 2 series `NET-LEAD` and pooled `R` ≥ $0.50 | nominate the `NET-LEAD` series for the incentive universe (the S2 live test or a later epoch) — owner decision |
| `NO-LEAD` | pooled `R` < $0.15, or any `FILL-TRAP` with no `NET-LEAD` | study 5's winners were a one-off; election ladders get no special treatment |
| `MIXED` | otherwise | report the `NET-LEAD` series, if any, as single-series nominations; no family claim |

A model-sensitive (`*`) `NET-LEAD` still counts, reported as such; `conservative` decides.

## 6. Read

- **2026-12-08 16:57Z**, a reminder in the session that built this. Read once, never earlier.
  The 4-hour research check-in may report row counts for these series (counts only).
- Calendar row in `docs/RESEARCH_CALENDAR.md`. The result goes in §8 and a journal entry.

## 7. Known limits (fixed now)

- The reward is the collector's estimate, not a payout. `LIMM_PAYOUT_TRUTH.md` asks whether
  payouts can be read without screenshots; a `FAMILY-LEAD` is still an estimate until paid.
- Nomination ladders are long-dated. Three weeks see few news shocks, so a quiet window can
  flatter fills; the 6 h markout and the conservative model are the guard, as in study 5.
- The extra ~80 markets add snapshot rows (about a third more collector volume) until the
  expiry.

## 8. Results

(Filled in once on the read date.)
