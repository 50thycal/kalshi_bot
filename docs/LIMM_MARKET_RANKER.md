# LIMM market ranker: which incentive markets pay most right now, and how we measure it

**What this is:** a read-only screen, `scripts/limm_market_ranker.py` (ops-allowlisted), that the
owner asked for on 2026-11-08:

> "We should always be seeking the best possible place to do this strategy. So what are the best
> ones right now? And how do we measure which ones are the best? … find when data is posted, and
> leave right before that data is posted."

It **ranks; it never decides.** Moving the live book to new markets is an owner decision and a new
epoch. Run it any time:

```
{"type":"script","name":"limm_market_ranker","args":[],"id":"rl-rank-<date>"}
```

## 1. Success, from the owner's side

| # | Goal | How it is checked |
|---|---|---|
| G1 | A ranked list of the best markets now, in $/day per $10 bid | A real run on live data, compared with an independent census |
| G2 | A sound measure: per-contract reward, crowding and repricing risk, with no early read of a pre-registered study | Unit tests, hand-checked arithmetic, and the data cut-offs (§4) |
| G3 | When each market reprices (data releases), with a suggested step-aside | The detected clock compared with known publication schedules |
| G4 | Numbers that can be trusted | Cross-checked against the collector's own scoring code, and limits stated |
| G5 | Easy to read and act on | A one-page view and a plain summary |

## 2. The measure

- **Reward per $10 bid per day:**
  - pool per day × share ÷ 2 (study 3's estimator), where share = q ÷ (field + q).
  - q is the number of contracts $10 buys at that side's best bid (at most 1,000 at 1¢).
  - The field is the collector's weighted score of every other resting order on that side.
- **Why per contract matters.** Kalshi pays by contracts resting near the best bid (LIP rules
  R4–R6, `kalshi_bot/liquidity_incentive/scoring.py`). $10 at 1¢ is 1,000 contracts; $10 at 50¢ is
  20. That is why the cheap side wins whenever its field is not crowded (study 9,
  `LIMM_BAND_PROBE.md`).
- **One tick behind.** The live book rests one tick behind the best bid (§9.72). The "behind" column
  multiplies the reward by the programme's discount factor; at a 1¢ best bid there is no tick behind.
- **What is not ranked:**
  - markets whose snapshot does not qualify (a side below target pays nobody);
  - markets inside the 48 h step-aside before close.
- **Repricing risk (fills).** A resting bid gets filled when the market reprices.
  - A **jump** is an hourly mid move of 8¢ or more.
  - Jumps less than 12 h apart in a series are one **episode**: one release moves every strike, and
    its effects drift over hours.
  - The risk grade is episodes per 30 observed days over the last 60 days: LOW under 1, MED under 3,
    HIGH otherwise.
  - **Turnover** is contracts traded per market-day divided by the competing size on our side. It is
    a rough measure of flow fills.
- **Market-level risk.** The top table grades each market on its own repricing episodes. A
  far-from-the-money strike rarely moves even when its series is busy. Where the market has fewer
  than 3 days of history, the series grade is shown, marked `*`.
- **Repricing clock.** The commonest UTC hour (±1 h) at which episodes start, shown once there are 3
  or more episodes.
- **Known releases.** Where the publisher's schedule is verified, the next release is shown and the
  step-aside advice follows it rather than the clock:
  - state employment ladders (`…EMP`): BLS, 10:00 ET;
  - AAA gas: Kalshi's 10:15–11:00 ET expiry.

## 3. What decides, and what does not

The ranker shows estimated **reward** and repricing **risk** side by side. It does not net them,
because the per-series **fill cost** is exactly what study 5 (`LIMM_FILL_HAZARD.md`) measures and
reads once on 2026-11-13. After that read, the ranker can take study 5's per-series fill cost and
rank by net.

## 4. No early reads

- The ranker simulates no fills and computes no P&L.
- It reads book snapshots (prices and field scores) and trade counts only. That is market data, not
  any pre-registered outcome: studies 5, 6, 8 and 9 measure simulated fills and markouts.

## 5. Known limits

- **The reward estimate has never been checked against Kalshi's actual payouts** (thesis "day-one
  check 3"). It is internally consistent (§6, cycle 2), but external calibration is still open.
- **Hourly snapshots** miss repricings that revert within the hour.
- **Coverage.** Reward and risk exist only for the markets the collector watches (about 100–160).
  Most live pool money is outside that set (cycle 7), and is listed but not ranked.
- **History is short for monthly data.** The collector was down from 10-07 to 10-30, so a monthly
  release may appear only once or twice.

## 6. Build log (cycles)

Each cycle tests the result against the goals in §1, records what was wrong, fixes it and retests.

**Cycle 1: the core ranking (G1).**
- Built v1 and ran it end to end on live rows pulled through the ops db channel (164 markets).
- The total pool, $6,693/day, matches the independent census (`rv-band-1108`, $6.7k/day).
- Share arithmetic was hand-checked. Example: KXAAAGASM 4.10 YES at 9¢ is 111 contracts against a
  field of 2,860, a 3.7% share.
- The top series were the Aspen and Big Sky opening-date ladders, then AAA gas.
- Recorded problem: the measure ignored fill risk and release timing (G2, G3).

**Cycle 2: trust in the reward number (G4).**
- Cross-checked the ranker's share formula against the collector's full-book LIP scoring on 2,610
  shadow quotes in 144 markets (`rk-calib2-1108`). Collector over ranker: median 1.000, 10th–90th
  percentile 1.000–1.000.
- Recorded problem: the estimator has never been checked against real payouts. That stays a limit.
- Recorded problem: the live bot rests one tick behind the best bid, so the ranker now shows that
  reward too.

**Cycle 3: risk on real history (G2), run `rl-rank-run1-1108`.**
- The run covered 69k hourly snapshots over 60 days.
- Problems found:
  - Single-jump series printed "100%" clocks, which was misleading. A clock now needs 3 or more.
  - LOW jump risk hid heavy flow (Aspen trades about 5,000 contracts a day). Turnover was added.
  - "Resolves on one event" ladders read LOW until their event. That is recorded as a limit.
- Surprise: the "behind" reward is exactly half on every bid above 1¢. Every live programme has
  `discount_factor_bps` = 5000 (`rk-disc-1108`). One tick behind a best bid that sets the reference
  price therefore halves the score. That is the §9.72 rule's cost, now measured.

**Cycle 4: release clock against real schedules (G3), run `rl-rank-run2-1108`.**
- Validated:
  - KXWAAEROEMP jumped on Fri 09-18 at 14Z. That is exactly BLS's State Employment release (Aug
    data, 10:00 EDT).
  - AAA gas reprices at 16Z ±1 (69% of jumps), inside Kalshi's 10:15–11:00 ET expiry window.
- Problems found:
  - After the Sep 18 release, other state ladders kept repricing for about 39 h, mostly in thin
    overnight hours. An hour-of-day step-aside was therefore the wrong rule. It is replaced by
    known-release advice: pull before the release and stay off for about 48 h. The next BLS release
    is Fri 2026-11-20 15:00Z.
  - One release counted as several jumps. Jumps are now grouped into episodes, and the risk grade
    and clock use episodes.
  - Retest: the synthetic test showed a 24 h grouping would merge a daily release into one endless
    episode, so the gap is 12 h. The tests pin both cases.

**Cycle 5: the owner's view (G5).**
- Built a one-page board from the ranker's `--json` output and checked it at 1280 px (light) and
  400 px (dark) with Playwright.
- Problems found:
  - The page scrolled sideways on a phone (459 px wide at 400). A long code sample did not wrap.
  - Kalshi titles showed raw `**markdown**`.
  - Numbers had no thousands separators.
  - The tables were 25 and 30 rows deep, with step-aside text wrapping to 4 lines.
  - The $/day column was off-screen on a phone.
  - The "9 bids" fact needed "before fill losses".
- Fixes: wrapped code; stripped `**` from titles; added separators; cut both tables to 15 rows;
  hid secondary columns under 640 px; set a short "off Nov 20 15Z +48h" step-aside with no wrap.
- Retest: no sideways scroll at either width and no console errors. Published as an artifact.

**Cycle 6: per-market risk (G2), found in cycle 4's retest.**
- Problem: risk was graded per series. A 1¢ NO bid on "gas above $3.20" with gas near $4.15 almost
  never moves, but it inherited AAA's HIGH because other strikes reprice daily.
- Fix: the top table now grades each market on its own episodes, falling back to the series grade
  (marked `*`) below 3 days of history.
- Tests pin the flat, short-history and daily-moving cases.
- Retest (`rl-rank-run4-1108`): gas's far 1¢ strikes ($3.20, $3.50, $4.80) are now LOW, and the
  near-the-money $4.00 and $4.10 strikes stay HIGH.

**Cycle 7: coverage (G1), found in cycle 6's retest.**
- Problem: between two runs 16 minutes apart, the ranked set fell from 164 to 102 markets.
- Diagnosis (`rk-join-1108`, `rk-end-1108`): not a bug. 62 programmes ended at 05:00Z on Sun 11-08.
- Bigger problem found while checking (`rk-cov-1108`): the collector watches only 101 live markets,
  holding $4,429/day. It misses **3,459 live markets in 265 series, holding $55,874/day.**
  - The cause is how the collector picks its markets (`collector._reconcile`). It keeps at most
    150, ranked by **total** programme reward, which favours long-dated ladders.
  - That misses short weekly programmes that pay about $100/day per market: shipping chokepoints,
    Panama, "Trump act", and AP POTUS.
- Fix in the ranker: a "not watched" section lists those pools by series. There is no book data,
  so they get no share estimate.
- Changing which markets the collector watches is a live-worker change. That is for the owner
  and Live Ops, not this screen.
