# LIMM step-aside window: does quoting from 48 h to 24 h before close pay?

**Status:** PRE-REGISTERED 2026-11-03 (Research Lab), before either part ran. The owner asked
for it on 2026-11-03 ("do 1, backtest what you can first too").

Study 6 of the incentive-programme research (`docs/RESEARCH_REVIEW.md`). The probe is
`scripts/limm_step_aside.py` (read-only, ops-allowlisted). It reuses the construction of study 5
(`LIMM_FILL_HAZARD.md`) unchanged and moves only the window. It is **descriptive**: a
nomination is an owner decision, and a rule change on the live book is a recorded change.

## 1. Question

`Alimm1` cancels its resting bids 48 h before the event (thesis §9.63). Holding to 24 h is a
pending owner decision (LIMM 72-hour review, 2026-10-31). The Lubbock event paid $6.85, above
the ~$5 bar, but nobody has measured the fills in those last 24 hours.

> In the band from 48 h to 24 h before close, does a cheap-side bid at the live rule earn more
> in reward than its fills cost?

## 2. Construction (no lookahead)

- **Everything is study 5's §2 except the hour filter.** That covers the hourly snapshot,
  cheap side, live-rule size, reward estimator, thin-book exclusion, the `conservative`
  replay against the next clock hour's prints (`optimistic` as a sensitivity), the end of the
  window at the first fill, and the 6 h markout (half price if unmarked).
- **Band hour:** a qualifying hour whose time to `close_time` is at least 24 h and under 48 h.
  `close_time` stands in for the event time, as in study 3.
- **Base hour:** the same with time to close of 48 h or more. That is today's quoting window. It
  is reported beside the band as a comparison and never decides.
- **The band is simulated on its own:** a market enters the band with no fill carried over.

## 3. Pools and measures

- **Pools:**
  - `live` is today's universe (`MENTION` series and `KXYTVIEWSHIGH`), the pool that decides;
  - `mention` and `youtube` are the parts of `live`;
  - `all` is every series the collector watched, descriptive only.
- **Measures per pool:**
  - band markets, band events;
  - band reward, fill cost and fills;
  - **`R_band`** = (reward − fill cost) per band market-day, an event-clustered ratio estimate;
  - `R_base` for comparison.

## 4. Pre-registered verdict (pool `live` only)

| verdict | rule |
|---|---|
| `NOMINATE 24H` | `R_band` − 1.96·se > 0: quoting the band adds net money |
| `KEEP 48H` | `R_band` + 1.96·se < 0: the band loses money |
| `HOLD` | otherwise |
| `HOLD (accrual)` | the `live` band has fewer than 20 markets or 8 events |

- `NOMINATE 24H` makes the 24 h step-aside an **owner decision**. It is never applied
  automatically.
- A model-sensitive verdict is reported as such: one that differs under `optimistic`. It still
  stands, because `conservative` decides.

## 5. The two parts

- **Part A, backtest.** Window 2026-09-17T00:00Z → 2026-10-07T03:00Z, when the shadow
  collector last watched the top programmes with both snapshots and the trade tape.
  - The window ends before study 5's window opens, so this reads none of study 5's pre-registered data.
  - The live universe was thin in that window (study 3 §10: 4 live market-days at 48 h or more), so Part A is expected to read `HOLD (accrual)` on `live`.
  - Its `all` pool is the evidence it can give, and that pool is descriptive.
  - Command: `{"type":"script","name":"limm_step_aside","args":["--since","2026-09-17T00:00:00+00:00","--until","2026-10-07T03:00:00+00:00"],"id":"rl-s6-backtest-20261103"}`.
- **Part B, forward, read once on 2026-11-17.** Window 2026-11-03T03:00Z → 2026-11-17T15:00Z.
  - Command: `{"type":"script","name":"limm_step_aside","args":[],"id":"rl-s6-run1-20261117"}`.
  - Under `HOLD (accrual)`: one re-read on 2026-12-01 with `--until 2026-12-01T15:00:00+00:00`, same bars.

## 6. Known limits

- `close_time` is a proxy for the event start, so for mention events the true band sits earlier
  than measured.
- The list of limits in study 5's §5 applies unchanged.
- The live book is not in the band today. So the band's fills are what the book **would** have
  taken, measured on the public tape.

## 7. Results

### Part A — backtest, 2026-09-17 → 10-07 (`rl-s6-backtest-20261103`)

```
LIMM step-aside — the 48h->24h band before close, reward minus fill markout (pre-registered docs/LIMM_STEP_ASIDE.md)

window 2026-09-17T00:00 -> 2026-10-07T03:00; hourly snapshots (incl. mark tail) 86988; prints 87177
markets with band hours 532; with base hours 753; markets with prints 491

== pools (deciding model: conservative; R = net USD per market-day, ±se clustered by event)
  pool     window  mkts  evts   reward   fill $ fills       R   ±se   R opt
  live     band       0     0     0.00     0.00     0     n/a   n/a     n/a
  live     base       2     2     0.78     0.00     0   +0.19  0.09   +0.09
  mention  band       0     0     0.00     0.00     0     n/a   n/a     n/a
  mention  base       2     2     0.78     0.00     0   +0.19  0.09   +0.09
  youtube  band       0     0     0.00     0.00     0     n/a   n/a     n/a
  youtube  base       0     0     0.00     0.00     0     n/a   n/a     n/a
  all      band     532   149  1076.18   267.69    80   +1.48  0.18   +0.51
  all      base     753   231  1636.11   258.47    51   +0.43  0.07   +0.26

== VERDICT (pool `live`, band; descriptive — a nomination is an OWNER decision)
  HOLD (accrual)
  `all` band, descriptive only: NOMINATE 24H
```

- **Verdict on `live`: HOLD (accrual), as expected.** The shadow window held no live-universe
  band hours. Part B decides.
- **`all` pool, descriptive only.** Across the 149 events of the top-150 programmes the
  collector watched, the band earned **+$1.48 per market-day net of fills (±0.18)**. Today's
  ≥ 48 h window earned +$0.43 (±0.07).
  - The band paid more reward per day, and its fills cost more: 80 fills in 532 markets, against
    51 in 753.
  - Under `optimistic` fills the band drops to +$0.51, so the lead is sensitive to the fill
    model.
- **What this does and does not say:** quoting closer to close was net positive on that tape,
  which was mostly sports ladders, not mentions. It says nothing yet about mention events, where
  informed flow near the event is the risk. Part B (2026-11-17) reads the live universe.
