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

*(none yet)*
