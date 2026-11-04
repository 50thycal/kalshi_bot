# LIMM fill early-warning: does the book show signs an hour before a bid is filled?

**Status:** PRE-REGISTERED 2026-11-04 (Research Lab), before either part ran. The owner asked
for it on 2026-11-03 ("run all those … and backtest as you can").

Study 8 of the incentive-programme research (`docs/RESEARCH_REVIEW.md`). The probe is
`scripts/limm_fill_warning.py` (read-only, ops-allowlisted). It is **descriptive**: a "pull the
bid" rule it supports is an owner decision and a change to the live book, never automatic.

## 1. Question

Fills, not rewards, decide the book's net (`LIMM_FILL_HAZARD.md` §1). Several of the losses came
in fast, for example DRA Dec on 11-03, where the YES bid went from 76¢ to 97¢ inside a minute of
the fill.

> Can a signal visible in the hour before a fill have predicted it? If so, would pulling the bid
> in warned hours have saved more in fill losses than it gave up in reward?

## 2. Construction (no lookahead)

- **Hours, size, reward and fill replay:** study 5's §2, unchanged. That covers the hourly
  snapshot, cheap side, live-rule size, reward estimator, thin-book exclusion, the
  `conservative` replay against the next clock hour's prints, and the 6 h markout (half price if
  unmarked).
  - The difference: **each qualifying hour is scored on its own.** A fill does not end the
    market's window, because the question is per-hour prediction.
  - Only hours at least 48 h before close count, today's quoting window (study 6 owns the band).
- **Features,** read from the snapshot of hour *h* and the same market's snapshot of hour *h*−1.
  Both are known before the trades of hour *h*+1 that score them.
  - `trades_last_5m` (prints in the 5 minutes before the snapshot);
  - `price_range_5m_cents`;
  - the cheap side's depth at best against hour *h*−1.
- **Primary warning (fixed now):** warn when `trades_last_5m ≥ 1`, or when the cheap side's depth
  at best fell by half or more since hour *h*−1. Each feature alone is reported descriptively and
  never decides. A missing feature counts as no warning, and the null share is printed.
- **Policy compared:** pull the bid in warned hours. The gain is the fill cost avoided in warned
  hours minus the reward given up in them, clustered by event.

## 3. Pre-registered verdict (pool `all`; `live` reported)

- **Floor:** at least 30 fills and at least 10 events with a fill, else `HOLD (accrual)`.
- **`USEFUL`:**
  - the event-clustered gain has LCB > 0;
  - **lift** (fill rate in warned hours ÷ fill rate in unwarned hours) is 2.0 or more;
  - at least 20 fills fall in warned hours.
- **`NO-SIGNAL`:** gain UCB < 0, or lift under 1.5.
- **`HOLD`:** otherwise.
- **`USEFUL` nominates a "pull the bid on warning" rule** for an owner decision, with the
  forward part as its confirmation.

## 4. The two parts

- **Part A, backtest.** Window 2026-09-17 → 2026-10-07T03:00Z, the shadow window with snapshots
  and the trade tape. Its data does not overlap any pre-registered read.
  - Command: `{"type":"script","name":"limm_fill_warning","args":["--since","2026-09-17T00:00:00+00:00","--until","2026-10-07T03:00:00+00:00"],"id":"rl-s8-backtest-20261104"}`.
- **Part B, forward, read once on 2026-12-11.** Window 2026-11-27T15:00Z → 2026-12-11T15:00Z.
  - It starts after study 5's latest possible window (its re-read ends 11-27). Study 6's band hours are excluded by construction.
  - Command: `{"type":"script","name":"limm_fill_warning","args":[],"id":"rl-s8-run1-20261211"}`.

## 5. Known limits

- **Hourly snapshots** see only the last state before the hour ends. A burst inside the resting
  hour is in the score, not in the warning.
- **Each hour is scored independently,** so one market's fills in consecutive hours count
  separately.
- **Pulling the bid also loses queue position.** That cost is not modelled.

## 6. Results

### Part A — backtest, 2026-09-17 → 10-07 (`rl-s8-backtest-20261104`)

```
LIMM fill warning — snapshot signals one hour before a resting bid's fill (pre-registered docs/LIMM_FILL_WARNING.md)

window 2026-09-17T00:00 -> 2026-10-07T03:00; hourly snapshots (incl. mark tail) 86988; prints 87177
qualifying hours 40573; markets 753; markets with prints 359

== pool `all` (conservative fills)
  hours 40573 (warned 1321); fills 110 (warned 61) in 30 events; feature nulls: trades_5m 0, depth 4157
  fill rate warned 0.046 vs unwarned 0.001; lift 36.99
  pull-on-warning: fill cost avoided $233.77, reward given up $563.44; gain -329.67 ± 147.66 (events 184)
  single features (descriptive): depth_drop>=50%: 48/1133 filled; range_5m>=2c: 5/10 filled; trades_5m>=1: 25/252 filled
  VERDICT (decides): NO-SIGNAL

== pool `live` (conservative fills)
  hours 12 (warned 2); fills 0 (warned 0) in 0 events; feature nulls: trades_5m 0, depth 2
  fill rate warned 0.000 vs unwarned 0.000; lift n/a
  pull-on-warning: fill cost avoided $0.00, reward given up $0.16; gain -0.16 ± n/a (events 1)
  single features (descriptive): trades_5m>=1: 0/2 filled
  VERDICT (descriptive): HOLD (accrual)
```

- **Verdict (`all`): NO-SIGNAL.** The pre-registered rule is the gain's UCB < 0. The gain was
  −$329.67 ± 147.66, so the UCB is about −$40.
- **The warning predicts fills.**
  - Warned hours filled at 4.6%, against 0.1% unwarned: a lift of 37.
  - 61 of the 110 fills fell in only 3% of the hours.
- **But pulling the bid there costs more than it saves.**
  - It avoids $234 of fill cost and gives up $563 of reward.
  - The warned hours are the active, high-reward hours.
- **Descriptive only** (these cannot be re-scoped into this verdict):
  - the 5-minute price-range trigger (≥ 2¢) fired 10 times and 5 filled;
  - the depth-halving trigger was the broad, expensive one: 48 fills in 1,133 hours.
  A narrower rule, such as a price-range or combined trigger, would be a **new** pre-registered
  test, not a revision of this one.
- **`live`:** no data in this window (12 hours), as with study 6.
- **Part B still reads once on 2026-12-11, unchanged.**
