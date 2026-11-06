# Idea-model run 2026-11-06: short-window price action, and what else we have not tried

**Scope.** This run was scoped by Calvin's request, so the Phase 0.5 menu was skipped. The request asked what we have
not tried beyond the MM cell and the liquidity-incentive markets. It named one idea, price action on very frequent
short windows (5, 10, 30 and 60 minutes), and asked for at least two others. Session role: Research Lab.

**Result.** 15 candidates were screened across four directions. **One promotion was run to a verdict
the same day: MOMO-H → KILL** (§A1, pre-registered in `MOMO_HOURLY_PREREG.md`): the hourly BTC ladder
already prices recent price action. **One fired hold cleared its
census: GRIDPIN** (§B1, results in `GRIDPIN_THESIS.md` run 3). One killed premise was **reopened by
board drift**: the 07-11 run's XVENUE15 kill reason, "Polymarket has no 15-min crypto", is no longer
true (§C1). Everything else is HOLD or KILL, with reasons given.

Nothing here changes a lifecycle state, a gate, a verdict or a book. Experiment OS stays canonical.

---

## Phase 0: grounding

- **Experiment OS** (`rl-ct-1106a`, as of 2026-11-05 20:36 CST): the portfolio is MMSELL, the
  liquidity-incentive line and theta (`theta-tail-sell` collect-only). **No open experiment touches a
  short-window venue**, so nothing below duplicates an open line.
- **Short-window history** (none of it is re-proposed):
  - **PIN15** (endgame 60 s-average pin on 15-min BTC) PASSED its probe, then was **RETIRED 07-16**
    when the live T-window slice showed +0.27¢ in its target window and −53¢ at 60–120 s before close.
  - **THETA** (hourly-ladder tail-sell) was SHELVED for a distribution-shape model error.
  - **TFAV** (hourly favorite-buy) was KILLED at −3.6¢.
  - **XLOCK-P2** (touch vs terminal) was KILLED; **METALHALT** (metals halt pin) was KILLED.
  - **MOM15, MMFLIP and XVENUE15** were killed **by argument only** on 07-11. They were never measured.
  - Simple TA books (`momentum`, `reversion`) lost about −5¢ over 1,000+ paper trades in the earliest phase.
- **The open question the record names:** `RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.5 and
  `RESEARCH_THETA_REMEDIATION.md` §7.1 say "the momentum/regime hypothesis remains untested", and
  telemetry for it (`trailing_move_*`) was added 10-28. That is where the user's idea meets the record.
- **Priors carried in.** Obs-pin and mechanics-blindness is the only family that has ever passed.
  Price-history edges on liquid markets start low. Latency races against fast makers are dead for a
  1–3 minute scan. Cross-venue divergence is usually below the 2–4¢ round trip.

## Phase 1: board, scoped to short windows

| venue | what it is | read |
|---|---|---|
| `KXBTC15M` + ETH/SOL/XRP/DOGE/BNB/HYPE twins | 15-min up/down, settles on the CF Benchmarks 60 s average | deepest short-dated crypto contract; PIN15's venue |
| `KXBTCD` hourly | "BTC above strike at :00", about 180 strikes per hour | **we already record it**: `crypto_ladder_snapshots` (5.8 M rows since 08-01 across the BTC/ETH ladders; KXBTCD ≈ 37k rows/day), about 8 captures/hour, **1.01¢ average spread** (one-day sample, 11-04, `rl-momo-size-1106d`) |
| `KXGOLD15M`, `KXGOLDH` | 15-min gold up/down (launched **Aug 2026**) and hourly gold ladder | per Cointelegraph, 15-min gold out-traded ETH contracts within weeks; ~3 months old, so it passes the 2-month venue-age gate |
| Polymarket `btc-up-or-down-15m` / `-5m` | same quarter-hour windows, 0.1–1¢ spreads | **exists since at least 2026-02** (`rl-pm15-1106k`); voids the 07-11 XVENUE15 kill reason |

**Short-window census** (`short_window_census --days 7`, ops `rl-swc-1106n`, code `7734679`, 2026-11-06).
Contract volume is from Kalshi `volume_fp`. A settled count of 6,000 is the page cap and reads as a floor.

| series | settled / day | contracts / day | median per market | open spread |
|---|---|---|---|---|
| `KXBTC15M` | 94.9 | 307.9 M | **3.23 M** | 1¢ |
| `KXETH15M` | 94.9 | 22.0 M | 220 k | 1¢ |
| `KXXRP15M` / `KXSOL15M` / `KXDOGE15M` / `KXHYPE15M` / `KXBNB15M` | 94.9 | 12.5 / 10.3 / 6.4 / 4.9 / 2.9 M | 125 / 102 / 63 / 46 / 27 k | 1–2¢ |
| `KXGOLD15M` | 63.3 | 31.4 M | **488 k** | 1¢ |
| `KXSILVER15M` | 63.3 | 4.3 M | 65 k | 1¢ |
| `KXBTCD` (hourly ladder) | ≥ 857 (cap) | ≥ 8.6 M | 0 (most strikes untraded) | 1¢ |
| `KXETHD` / `KXGOLDH` / `KXSILVERH` / `KXWTIH` | ≥ 857 (cap) | 0.20 / 0.30 / 0.16 / 0.10 M | 0 | 1–2.5¢ |
| `KXINXU` / `KXNASDAQ100U` (index) | 371 / ≥ 857 | 1.45 / 0.41 M | ~0 | 40¢ / 12¢ (dead rungs) |
| `KXBTC5M`, `KXETH5M`, `KXEURUSDH`, `KXUSDJPYH` | **absent** | — | — | — |

Read: **capacity is not the constraint on the 15-minute venues.** BTC clears millions of contracts per
window, and 15-minute gold, three months old, already clears about half a million. **A 5-minute window
exists only on Polymarket**, and Kalshi lists no FX hourly series. The hourly ladders concentrate on
BTC; the ETH, gold and index ladders are an order of magnitude thinner.

**Instrument finding:** `kalshi_market_survey` cannot see rolling short-window series. It counts only
open markets, and a 15-min market lives 15 minutes (`rl-survey-1106h` showed Crypto as 1 series / 11
markets). `scripts/short_window_census.py` was added for that reason (see the census section below).

**Cost floor for taking on these venues** (q = 10, fee `ceil(0.07·q·P·(1−P)·100)/q`):

| entry price | fee/contract | + half of a 1¢ spread | hold-to-settle cost |
|---|---|---|---|
| 50¢ | 1.8¢ | 0.5¢ | **2.3¢** |
| 30¢ / 70¢ | 1.5¢ | 0.5¢ | **2.0¢** |
| 20¢ / 80¢ | 1.2¢ | 0.5¢ | **1.7¢** |
| 10¢ / 90¢ | 0.7¢ | 0.5¢ | **1.2¢** |

An exit before settlement doubles it. Any short-window edge must clear about 2¢ per contract after
the price it is taken at, which is why the pre-registered P2 bar is +1.5¢ *net*.

## Phase 2–3: slate and screen

Axes: **Corr** (shares a driver with a live book: − is worse), **Edge** (plausibility given the
meta-lessons), **Cost** (survives fee and spread), **Test** (gradeable now), **Cap** (absorbs size,
settles often), **Reuse**.

### Direction A: short-window price action (the user's idea)

| # | candidate | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| A1 | **MOMO-H**: is the hourly BTC ladder's *quote* miscalibrated given the last 15 min of spot? (follow or fade) | ++ | o | o | ++ | + | ++ | **PROMOTE → probe run** |
| A2 | VOLCLOCK: do hourly strikes underprice moves in scheduled vol windows (8:30 ET prints, 9:30 open, 16:00)? | + | o | o | + | + | ++ | **HOLD**: same table, but pre-registered only after A1's verdict, so the same rows are not mined twice |
| A3 | MOMO-15: A1's question on the 15-min up/down at T≈7 min | ++ | o | − | + | ++ | + | **CLOSED by A1's KILL**: pre-registered to run only on an A1 signal; a 50¢ fee zone makes it strictly harder |
| A4 | GOLD15 momentum / endgame | ++ | − | − | + | + | + | **KILL (endgame), fold (momentum into A3)**: PIN15's endgame was falsified live against Pyth-fed makers; METALHALT closed the halt premise |
| A5 | MOM-DIRECTION: forecast BTC's own direction from candles | ++ | −− | − | ++ | ++ | + | **KILL**: random walk on priors; the early TA books lost the cost floor over 1,000+ trades |
| A6 | ALTLAG: BTC spot leads alt 15-min quotes | + | + | − | + | − | + | **KILL for us**: a latency race; our scan takes 1–3 min (SIBLAG precedent) |

### Direction B: outcomes partly known hours early (the only family that has passed)

| # | candidate | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| B1 | **GRIDPIN**: ERCOT daily-peak ladder settles on the *hourly-integrated* peak while the dashboard shows 5-min peaks | ++ | + | + | ++ | o | + | **FIRED HOLD → census CLEARS (run 3)** → build the full probe P1–P4 |
| B2 | GRID-SIBLINGS: PJM / MISO / ERCOT-North peak series found by the census | ++ | + | o | − | o | + | **HOLD**: extension after B1's P1; universe unsized |
| B3 | EARNBEAT re-run | ++ | o | o | o | o | ++ | **scheduled week of 11-09** (unchanged hold) |
| B4 | TOKENPIN | ++ | o | − | −− | −− | − | parked until about mid-January 2027 (unchanged) |

### Direction C: the same window on two venues

| # | candidate | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| C1 | **XVENUE15/5**: Kalshi `KXBTC15M` vs Polymarket `btc-up-or-down-15m` on the identical quarter hour | ++ | − | − | + | + | + | **HOLD (premise reopened)**: the two settle on different sources (CF Benchmarks 60 s average vs Polymarket's oracle), so a gap is partly basis, not mispricing; cross-venue gaps have been 1–1.6¢ against a 2–4¢ round trip; XGAME's shared-feed symmetry. Cheapest test is a divergence census from both venues' public price histories. Run it only if the user wants this direction |
| C2 | XVENUE-GOLD: Kalshi gold 15-min vs a gold perp/spot | + | − | − | − | + | o | **KILL**: a latency race against Pyth-fed makers |

### Direction D: things that look new but are not

| # | candidate | call |
|---|---|---|
| D1 | RESOLVE-DRIFT (buy decided outcomes at 97–99¢ before settlement) | KILLED 09-29: per-order fee ceil plus lockup |
| D2 | Options-implied distribution vs index or commodity ladders | crypto version efficient (Deribit); CME/SPX version parked on paid data (OPTRV) |
| D3 | Kalshi perps carry / passive perp | operator line already (PERP-V1, PASSIVE-PERP, SPOT-PERP-CARRY), not regenerated |

**Tally:** 15 screened. 1 promotion run to a verdict (A1: KILL). 1 fired hold cleared (B1). 3 holds (A2, B2, C1, of which C1 is a reopened premise). A3 closed by A1. 1 scheduled (B3). 1 parked (B4). 4 new kills (A4 endgame, A5, A6, C2).
3 restatements of existing verdicts (D1–D3), which are not new kills.

## A1 MOMO-H: verdict — KILL

Pre-registered in `MOMO_HOURLY_PREREG.md` (02:57Z, before any result query). Three runs, all disclosed
there:

1. `rl-momo-run-1106m` read **0 rows**: production collates `en_US.utf8`, so the `~` day range was empty
   (PR #739, amendment A2).
2. `rl-momo-run2-1106p` gave **HOLD (instrument)**: 29.6% of decisions had no official label (Kalshi's
   historical cutoff). Its statistics were seen.
3. `rl-momo-run3-1106q` (09-01..11-05, amendment A3, results-aware) gave **KILL**: S = **−0.67¢** (90% CI
   −5.76 to +4.44), implied trade **−1.63¢** net (p5 −4.24), all ten taker legs negative after fee,
   66 days / 5,610 decisions / 0.16% unlabeled.

**What it means for the user's idea.** The hourly BTC ladder already prices the last 15 minutes of
movement. Neither following nor fading recent price action covers the ~2¢ cost of taking. Together with
the early TA books (−5¢ over 1,000+ trades) and PIN15's retirement, **short-window price action on
Kalshi crypto is closed for a 1–3 minute bot**. MOMO-15 was conditional on a signal here and is
closed. VOLCLOCK tests a different mechanism (scheduled volatility, not recent movement) and stays a
HOLD; it should not be run as a way to rescue this idea.

**Platform finding (out of mission → OWNER DECISION, not fixed here).** Kalshi's live `/markets` stops
returning settled markets older than a moving cutoff (target about 3 months; docs.kalshi.com
"historical data"); they move to `/historical/markets`. Any repo script that grades older settlements
through live `/markets` silently loses data. It shows up as missing labels, not as an error (0 fetch
errors here). Recommended route: an Experiment OS issue owned by Platform Change Review / Live Ops.

## Holds queue: reconciled (as of 2026-11-06)

| hold | trigger | state |
|---|---|---|
| **GRIDPIN** | settled day count ≥ 60 | **FIRED and CLEARED 11-06** → full probe P1–P4 is the next build |
| **EARNBEAT** | Q3 reports settle | due week of 2026-11-09 |
| **ECON-REACT** | genuine prints settled | still thin (13 settled at 09-29); re-check monthly |
| **VOLCLOCK** (new) | A1 verdict recorded | **trigger met → pre-registered 11-06** (`VOLCLOCK_PREREG.md`, `scripts/volclock_probe.py`); a different mechanism, not a rescue |
| ~~MOMO-15~~ | A1 shows a signal (P1) | **closed**: A1 KILL |
| **XVENUE15/5** (reopened) | operator wants a cross-venue direction | census-ready (public price histories on both venues) |
| **GRID-SIBLINGS** (new) | GRIDPIN P1 reads | conditional |
| **TOKENPIN** | ≥ 20 settled weeks plus partials | about mid-January 2027 |
| **HURR** | first landfall-threat storm | **retire if nothing by 2026-11-30** (unchanged) |
| others (RTPIN/BOXPIN, STREAMPIN, OPTRV, COMPIN, MENTION-corpus, XLOCK-P1, RATELAG, ART, EQUITY-HUB) | unchanged | parked |

## How this run was checked (build, test, review cycles)

Success, from Calvin's side, was fixed first: (1) no re-run of a settled idea without naming the material
difference; (2) every call grounded in current board data and settled-sample counts; (3) honest per-idea
cost math; (4) a ranked call with the cheapest falsifying test and a kill line written before results;
(5) something explorable plus durable repo state. Each cycle below tested against those goals.

| # | focus | what was checked | findings, and what changed |
|---|---|---|---|
| 1 | grounding / novelty | every candidate grepped against the scorecard, journal and idea-model docs | Deribit replication already efficient; TSA ladder extinct; tweet/view counters killed as velocity extrapolation; **RESOLVE-DRIFT (decided-outcome carry) killed 09-29**, so dropped; MOM15/MMFLIP/XVENUE15 had been killed by argument only; theta §2.5 named momentum as untested, which became A1 |
| 2 | data reality | control tower, table census, board survey, GRIDPIN census, Polymarket listing | month and 10-day SQL reads hit the 30 s ops timeout, so a chunked probe was needed; the final-mid label was often missing, so amendment A1 switched to the official result; **the board survey cannot see 15-min markets**, so `short_window_census` was built; GRIDPIN cleared; Polymarket 15m/5m confirmed |
| 3 | cost + first quantitative read | fee math, then the probe's real run | cost floor about 2¢ held per-idea; probe tests (unit, real Postgres, stubbed `main()`) passed, yet **the production run read 0 rows**: prod collates `en_US.utf8` and the `~` range was empty (confirmed `rl-momo-coll-1106o`, reproduced under ICU en-US, fixed by exact tickers, amendment A2, PR #739) |
| 4 | visual / UX | the page rendered in Chromium at 1200 px and 390 px, light and dark, plus overflow and console checks | phone chart text unreadable (SVG scaled), so charts are now drawn at true pixel width; orphan stat tile, so a fixed 3/2-column grid; with results injected (synthetic, local only) **the page scrolled sideways on phone** (verdict chip and stat values), fixed; the key net-EV table columns were off-screen on phone, so reordered with a swipe hint; venue table clipped a column at desktop, so tickers moved under names |
| 5 | decision-readiness + honesty | the doc and page re-read against the raw query outputs | slate count said 16, actually 15; "1.0¢ median spread" was a one-day *average*; "5.8 M quotes on the BTC ladder" was the whole table; the page claimed "tested today" before any result existed, so it is now conditional on a real result. All corrected |

## Ops requests made by this run (all read-only)

`rl-ct-1106a` (control tower); `rl-momo-census-1106c`, `rl-momo-size-1106d`, `rl-momo-d1-1106g`
(table census and plumbing; the month and 10-day SQL reads `-1106e`/`-1106f` timed out at 30 s);
`rl-survey-1106h` (board survey); `rl-gridpin-1106i` (GRIDPIN census run 3); `rl-pm15-1106j`/`-1106k`
(Polymarket short-window listing check); `rl-swc-1106n` (short-window census); `rl-momo-run-1106m` (probe run 1: 0 rows, collation defect); `rl-momo-coll-1106o` (collation confirmation); `rl-momo-run2-1106p` (probe run 2: HOLD, labels); `rl-momo-run3-1106q` (probe run 3: KILL).
