# LIMM fill hazard — which incentive series pay net of the fills a resting bid takes

**Status:** PRE-REGISTERED 2026-11-01 (Research Lab), before any run. Nothing in the window was
read except collector row counts (the 4-hour check-in). **Read once on 2026-11-13** (see §7).

Study 5 of the incentive-programme research (the first from the 72-hour research review,
`docs/RESEARCH_REVIEW.md`). Probe: `scripts/limm_fill_hazard.py` (read-only, ops-allowlisted).
Book: `Alimm1` (`docs/LIQUIDITY_INCENTIVE_THESIS.md`). Experiment OS is canonical. The probe is
**descriptive**: it ranks and nominates. Any universe or rule change is an owner decision,
recorded as a new epoch.

## 1. Question

Study 3 v2 (`LIMM_PROGRAMME_OUTCOMES.md` §10) found series outside the live universe that pay
about twice the reward per market-day. It could not say whether they pay **net**, because a
resting bid gets filled and a fill costs money. On the book's own record, fills cost more than
rewards paid: −$105 of fills against $86 of rewards, lifetime (scorecard 2026-10-31).

> For each series the shadow collector watches, what does a cheap-side bid at the live rule
> earn per market-day in reward, **minus** what the fills it would have taken cost?

This targets the two ways to improve the programme: better markets (more net per market-day),
and avoiding the markets whose fills eat the reward.

## 2. Data and window (no lookahead)

- **Window:** 2026-10-30T16:00Z (the shadow collector back on, `rl-limm-shadow-on-20261030`)
  → 2026-11-13T15:00Z. Fixed now.
- **Hours:** `incentive_market_snapshots`, the last valid snapshot per market per clock hour,
  joined to its programme row. A qualifying hour and its cheap side, price, size and reward use
  `LIMM_PROGRAMME_OUTCOMES.md` §3 unchanged: both sides at Target Size, programme active,
  liquidity type, ≥ 48 h before close, cheap side 1–10¢, $20 at ≥ 4¢ else $10, ≤ 500
  contracts, reward = pool/hour × qty/(field + qty) / 2, hours with share > 0.5 excluded from
  reward and counted.
- **Fills:** the public trade tape (`incentive_trade_events`), timed by the exchange timestamp
  (`ts_ms`, else `received_at`). The bid placed on hour *h*'s snapshot rests through the next
  clock hour *h*+1 and meets only that hour's prints, so price and queue are known before the
  trades that score them.
- **Fill model: `conservative`** (`kalshi_bot/liquidity_incentive/fills.py`). We join the back
  of the cheap side's best level. The depth at best in the snapshot is ahead of us, and
  only prints at our price, or through it, on our side consume it. The `optimistic` model (any
  touch fills us fully) is reported beside it as a sensitivity. It never decides.
- **After a simulated fill,** that market earns nothing more in the window. §9.70 cancels the
  rest of the bid, and the book does not re-enter a market it holds.
- **Fill cost (markout):** filled contracts × (fill price − the same side's best bid 6 h later),
  from the snapshot of hour *h*+7. If that hour has no snapshot, the nearest one within
  *h*+5…*h*+9 is used. If none exists, the fill is charged half its price, the live stop's
  trigger. A bid that rose counts as a gain. Fees are excluded (§5).

## 3. Measures (per series)

- **market-days:** distinct (market, date) over qualifying hours.
- **reward:** the summed reward over each market's hours up to its first fill.
- **fill loss:** the summed markout cost of the fills.
- **net per market-day `R`** = (reward − fill loss) / market-days. It is a ratio estimate with
  an event-clustered standard error (linearised: residual per event = net − `R` × market-days).
- **fill rate:** fills per 100 market-days.

## 4. Pre-registered labels and verdicts

Floor per series: **≥ 20 market-days and ≥ 5 events**, else `THIN`.

| label | rule |
|---|---|
| `NET-LEAD` | `R` ≥ $0.50 and `R` − 1.96·se > 0 |
| `FILL-TRAP` | reward per market-day ≥ $0.50 and `R` ≤ 0 |
| `NO-LEAD` | every other series at the floor |

- **V0 — instrument.** If fewer than 3 series reach the floor, the verdict is **HOLD
  (accrual)**. There is one re-read on 2026-11-27, with the window end moved to
  2026-11-27T15:00Z and the same bars.
- **V1 — better markets exist.** Any series outside the live universe that is `NET-LEAD`, with
  `R` above the live universe's pooled `R`, is **nominated**. A nomination is an owner decision
  to add the series as a new epoch, a live change, never automatic.
  - The live universe keeps today's definition: `MENTION` series and `KXYTVIEWSHIGH`.
- **V2 — markets to avoid.** Any series inside the live universe that is `FILL-TRAP` is
  **nominated for removal**. This is also an owner decision.
- **Model-sensitive:** a series whose label differs under `optimistic` is marked `*`. A
  nomination that is model-sensitive is reported as such. It still stands, because
  `conservative` decides.

## 5. Known limits (fixed now, not re-scoped later)

- **Hourly placement.** Each hour is a fresh bid at the best bid. The live book rests longer
  and reprices.
- **Queue ahead counts only depth at best;** cancellations ahead of us are ignored. So the
  conservative model under-fills.
- **The markout at 6 h is a proxy for the stop's exit;** it is not the settlement.
- **Maker fees are excluded.** Fills are rare, so fees move `R` little. A nomination still goes
  through the owner and the book's own fee accounting.
- **The population is what the collector watches** (the top ~150 programmes by reward), not all
  of Kalshi.
- **The live universe's own hours count only when the collector snapshotted them.**

## 6. Cost

Read-only. One snapshot query and one trade query over 14 days (about 50k hourly snapshots and
a few thousand prints). The statement timeout is raised as in study 3.

## 7. Command (read once on 2026-11-13, after the LIMM exits-vs-hold read)

`{"type":"script","name":"limm_fill_hazard","args":[],"id":"rl-s5-run1-20261113"}`

A re-read under V0: `{"type":"script","name":"limm_fill_hazard","args":["--until","2026-11-27T15:00:00+00:00"],"id":"rl-s5-run2-20261127"}`.

## 8. Results

Not run.
