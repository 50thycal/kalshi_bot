# LIMM mid-band probe: do incentive bids above 10¢ pay, one-sided or two-sided?

**Status:** PRE-REGISTERED 2026-11-08 (Research Lab), before either part ran. The owner asked for it
on 2026-11-08: "we can do more expensive bands, like a 50 cent bid … sticking between the 0 to 10
band is really limiting … review what type of series we could get in, potentially different band
lengths, or just how we approach these incentive markets."

Study 9 of the incentive-programme research (`docs/RESEARCH_REVIEW.md`). The probe is
`scripts/limm_band_probe.py` (read-only, ops-allowlisted). It is **descriptive**. A rule it
supports, such as quoting above 10¢, two-sided quoting or a new universe, is an owner decision and
a new epoch on the live book. It is never automatic.

## 1. Why now (census, ops `rv-band-1108` and `rv-bandser-1108`, 2026-11-08)

- **The money has moved.** Live liquidity pools total about **$6,700/day**.
  - Mention markets hold about $3/day of that.
  - The rest is long-dated **state and economic-data ladders**, closing in 2027: AAA gas monthly,
    state jobs, port TEU, rent, visitors, ERCOT peak, nuclear.
- **The ≤10¢ side is crowded.** It holds 56% of the pool, but the field's score there is about
  80k (100k–700k on the econ ladders), against 1,000 contracts for a $10 bid at 1¢.
- **The 11–50¢ bands are much thinner.** They hold about $2,200/day across 49 markets. The field's
  score per side is about 2k–15k.
  - A $10 bid takes 1–3% of a side there.
  - Its estimated reward per $10 is similar to or better than the cheap side's.
- **Why the scoring makes the cheap side look better than it is:** reward is paid **per contract**
  (Kalshi LIP rules R4–R5, `kalshi_bot/liquidity_incentive/scoring.py`). So a 1¢ bid gets 50× the
  contracts of a 50¢ bid per dollar. But with the live book's fixed $10 per bid, the most a fill
  can lose is $10 in either band.
- **These markets move on monthly releases.** That makes them slow-information markets, the same
  premise as MMSELL's P-SLOW cell. Between releases, a resting bid's fills may be cheap.

## 2. Question

> In incentive markets where **both** sides bid 11–50¢, does a resting $10 bid at the best bid earn
> more estimated reward than its fills cost?
>
> Policies:
> - **CHEAP**: the cheaper side only;
> - **DEAR**: the dearer side only;
> - **BOTH**: both sides, $10 each.

## 3. Construction (no lookahead)

- **Hours.**
  - Study 3's hourly snapshot (`limm_programme_outcomes.py`), with its rules unchanged:
    - liquidity programme;
    - both sides meet target;
    - inside the programme period;
    - at least 48 h before close.
  - Plus the new band filter: the **cheaper side's best bid is 11–50¢**.
  - Those hours never have a side at 1–10¢. Studies 5, 6 and 8 (cheap side only) cannot share
    a single hour with this probe, so nothing pre-registered is read early.
- **Size and price.** Each leg rests at its side's best bid, with `floor(1000 / price)` contracts
  (a $10 bid).
- **Reward.**
  - Study 3's estimator per leg: pool per hour × our share / 2, where our share is
    qty / (field + qty) on that side.
  - A leg whose share exceeds 0.5 is "thin" and earns no estimated reward (study 3's rule).
- **Fills.**
  - The bid on hour *h* rests through clock hour *h*+1.
  - It is replayed against that hour's public prints with `ShadowLeg`. `conservative` decides;
    `optimistic` is a sensitivity.
  - A fill ends that leg's window in that market. In BOTH, the other leg keeps running.
- **Fill cost.**
  - The markout: the same side's best bid **24 h** after the resting hour. The nearest hour within
    ±2 h is used if the exact hour is missing.
  - An unmarked fill is charged half its price (study 5's rule).
  - A 6 h markout is reported descriptively.
- **Unit.** One **leg-day** is one leg quoted in one market on one UTC day; it is $10 of quote.
  - R = Σ(reward − fill cost) / Σ leg-days, per policy.
  - The standard error is clustered on the event, as in study 5.

## 4. Pre-registered verdict (per policy, conservative model, 24 h markout)

| verdict | rule |
|---|---|
| `HOLD (accrual)` | fewer than 100 leg-days, or fewer than 10 events |
| `PROMISING` | R ≥ $0.15 per leg-day **and** R − 2.39·SE > 0 |
| `FILL-TRAP` | reward ≥ $0.15 per leg-day **and** R ≤ 0 |
| `NO-LEAD` | R + 2.39·SE < $0.15 |
| `HOLD` | otherwise |

- **The bar.** $0.15 per $10 leg-day is about $4.50 a month per bid. Ten bids (today's $100 cap)
  would then make about $45 a month, half the north star.
- **The 2.39** is a Bonferroni correction for three policies.
- **What PROMISING leads to.** It goes to the owner as a candidate live trial (an owner decision).
  Part B is its out-of-sample check.
- **Reported, never deciding:** by band (11–25, 26–40, 41–50), by series, fill rate per 100
  leg-days, BOTH pairs where both legs filled, the 6 h markout and the optimistic model.

## 5. The two parts

- **Part A, backtest.** All collector data from 2026-09-17 to 2026-11-08T12:00Z: the shadow window,
  then the gap from 10-07 to 10-30, then data since the restart.
  - Command: `{"type":"script","name":"limm_band_probe","args":["--since","2026-09-17T00:00:00+00:00","--until","2026-11-08T12:00:00+00:00"],"id":"rl-s9-backtest-20261108"}`.
- **Part B, forward, read once on 2026-12-03.** Window 2026-11-09T00:00Z to 2026-12-03T15:00Z. Same
  bars.
  - Command: `{"type":"script","name":"limm_band_probe","args":[],"id":"rl-s9-run1-20261203"}`.

## 6. Known limits

- **The reward is an estimate.** It uses the dashboard estimator (assumptions A1–A4 in
  `scoring.py`), not confirmed payouts. Payouts are confirmed only from the owner's screenshots.
- **Capital lock.** A filled 11–50¢ position on a 2027-dated market ties up its $10 until it is
  sold or settles. The 24 h markout prices how far it moved, not the cost of the capital being
  locked. The count of fills is reported so that cost can be judged.
- **Hourly snapshots and queue position.** As in studies 5 and 8: the snapshot is hourly, and the
  depth at best is treated as ahead of us.
- **Data gap.** The collector was down from 10-07 to 10-30, so Part A has a gap.

## 7. Results

### Part A — backtest, 2026-09-17 → 11-08 (`rl-s9-backtest-20261108`)

```
LIMM band probe — $10 incentive bids where both sides bid 11-50c (pre-registered docs/LIMM_BAND_PROBE.md)

window 2026-09-17T00:00 -> 2026-11-08T12:00; hourly snapshots (band markets, incl. mark tail) 66134; band markets 792; prints 80778
qualifying band hours 49242; markets 638; series 98; events 230; thin leg-hours (no reward) 0
  by band: (11, 25) 26973h, (26, 40) 12752h, (41, 50) 9517h
  by week: 2026-09-14 6175h, 2026-09-21 12232h, 2026-09-28 15913h, 2026-10-05 4379h, 2026-10-12 259h, 2026-10-19 246h, 2026-10-26 2933h, 2026-11-02 7105h

== model `conservative` (DECIDES); 24 h markout decides, 6 h descriptive
  policy  leg-d evts  rew/ld loss/ld    R/ld   ±se   R6/ld fills /100ld unmk  verdict
  CHEAP    3551  230   +0.16   +0.09   +0.07  0.02   +0.07   140    3.9   47  NO-LEAD
  DEAR     3551  230   +0.06   +0.11   -0.04  0.02   -0.03   156    4.4   83  NO-LEAD
  BOTH     6888  230   +0.11   +0.10   +0.01  0.01   +0.02   269    3.9  127  NO-LEAD
  BOTH markets where both legs filled: 71 of 638
  by band (cheaper side's bid; descriptive):
    CHEAP  11-25: 2337ld R +0.10 fills 91 | 26-40: 579ld R +0.02 fills 27 | 41-50: 635ld R -0.01 fills 22
    DEAR   11-25: 2337ld R -0.07 fills 102 | 26-40: 579ld R -0.04 fills 28 | 41-50: 635ld R +0.04 fills 26
    BOTH   11-25: 4612ld R +0.02 fills 189 | 26-40: 1146ld R -0.00 fills 49 | 41-50: 1130ld R +0.02 fills 31
  by series, policy BOTH (descriptive), top 15 by leg-days:
    KXNFLLADDERREC             526ld  18ev rew +0.21 loss +0.18 R +0.03 fills 26
    KXNFLFFPTSLADDER           486ld  18ev rew +0.16 loss +0.14 R +0.02 fills 16
    KXBWAYATTENDANCE           356ld   1ev rew +0.08 loss +0.06 R +0.02 fills 10
    KXNFLLADDERRECYDS          338ld  18ev rew +0.15 loss +0.23 R -0.08 fills 23
    KXNYNJPORTTEU              288ld   1ev rew +0.03 loss +0.04 R -0.01 fills 7
    KXWAAEROEMP                248ld   1ev rew +0.05 loss +0.02 R +0.03 fills 4
    KXPADATACENTERS            248ld   1ev rew +0.08 loss +0.02 R +0.06 fills 10
    KXCITYCHAMPS               218ld   2ev rew +0.04 loss +0.00 R +0.04 fills 0
    KXMLBSEASONGAMES           206ld   1ev rew +0.13 loss +0.04 R +0.09 fills 5
    KXWALLSTBONUS              188ld   1ev rew +0.04 loss +0.03 R +0.01 fills 5
    KXNYCASKRENT               184ld   1ev rew +0.05 loss +0.03 R +0.02 fills 2
    KXNFLLADDERRSHYDS          182ld  17ev rew +0.17 loss +0.23 R -0.05 fills 12
    KXFLVISITORCOUNT           178ld   1ev rew +0.07 loss +0.02 R +0.05 fills 4
    KXBAA                      164ld   1ev rew +0.05 loss +0.03 R +0.02 fills 3
    KXNFLESCALATORREC          132ld  20ev rew +0.30 loss +0.29 R +0.00 fills 8

== VERDICT (decides: conservative, 24 h). PROMISING is an OWNER decision for a live trial, never automatic
  CHEAP  NO-LEAD
  DEAR   NO-LEAD
  BOTH   NO-LEAD

== model `optimistic` (sensitivity); 24 h markout decides, 6 h descriptive
  policy  leg-d evts  rew/ld loss/ld    R/ld   ±se   R6/ld fills /100ld unmk  verdict
  CHEAP    3551  230   +0.12   +0.19   -0.07  0.03   +0.02   233    6.6   97  NO-LEAD
  DEAR     3551  230   +0.04   +0.20   -0.16  0.03   -0.05   287    8.1  135  NO-LEAD
  BOTH     6888  230   +0.08   +0.19   -0.11  0.02   -0.01   473    6.9  224  NO-LEAD
  BOTH markets where both legs filled: 170 of 638
  by band (cheaper side's bid; descriptive):
    CHEAP  11-25: 2337ld R -0.08 fills 158 | 26-40: 579ld R -0.04 fills 37 | 41-50: 635ld R -0.05 fills 38
    DEAR   11-25: 2337ld R -0.21 fills 203 | 26-40: 579ld R -0.11 fills 44 | 41-50: 635ld R +0.01 fills 40
    BOTH   11-25: 4612ld R -0.15 fills 354 | 26-40: 1146ld R -0.06 fills 72 | 41-50: 1130ld R -0.01 fills 47
```

- **Verdict: NO-LEAD for all three policies.** Under the deciding conservative model, R + 2.39·SE
  stays below the $0.15 bar for each.
  - **CHEAP** is the only positive policy: +$0.07 per leg-day ± 0.02. It is positive, but at about
    half the bar.
  - **DEAR:** −$0.04.
  - **BOTH:** +$0.01.
- **The sample is large.** It covers 49k qualifying hours in 638 markets, 98 series and 230 events.
  Floors are met with room to spare.
- **Fills eat most of the reward.**
  - About 4 fills per 100 leg-days, costing about $2.30 each at the 24 h markout.
  - CHEAP's gross reward is +$0.16 per leg-day. Fills take +$0.09 of it.
  - The dearer side's reward is small (+$0.06), and its fills cost more (+$0.11).
- **Two-sided quoting does not hedge.** Both legs filled in only 71 of 638 markets.
- **Model-sensitive.** Under the `optimistic` fill model, which assumes more fills, every policy is
  negative.
- **Descriptive (cannot be re-scoped into the verdict):**
  - The 11–25¢ cheaper side is the best cell, at +$0.10 per leg-day.
  - The long-dated economic ladders net +$0.02 to +$0.09. They have low reward and few fills, and
    each is a single event, so they cannot be clustered.
  - The NFL ladders (shadow window) have high reward but high fill cost, netting about 0.
- **What it means.** Moving the book above 10¢ is not a fix. At best it earns about $2 a month per
  $10 bid, below the bar and dependent on the fill model. The incentive question that can still
  move the book is **universe**: whether the cheap side pays on the economic ladders, where the
  pool now is. Study 5 reads exactly that on 2026-11-13 (V1, outside-universe NET-LEAD series).
- **Part B** still reads once on 2026-12-03, unchanged, unless the owner drops it.
