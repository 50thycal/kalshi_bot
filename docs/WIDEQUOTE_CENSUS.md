# WIDEQUOTE — is there live flow inside the board's 55–92¢ spreads?

*Census specification written 2026-07-25, before any data was pulled; the census gates below are
pre-registered. Status: **CENSUSED 2026-07-26 — RULED OUT.** C1/C2 fail on every series tested,
both auto-discovered and the originally-named candidates. See RESULTS below. This is deliberately
a **census, not a probe** — on the `STREAMPIN` / `SEASONPIN` precedent, where the promotable
question was "does the instrument even trade?" rather than "is the edge real?".*

## One-liner

A handful of high-volume Kalshi series quote spreads of 40–92¢ — orders of magnitude wider than
anything the bot trades — and the census asks the only question that matters first: **is anyone
still crossing them?**

## Mechanism

- **What mispricing:** not a mispricing — a **liquidity vacuum**. `KXGOVCA` (92.0¢ avg spread,
  49.2M cumulative volume), `KXMAYORLA` (80.2¢, 87.2M), `KXPGATOUR` (55.2¢, 117.3M across 147
  markets, weekly cadence), `KXWTIMAX` (42.8¢), `KXBTCMAX150` (40.8¢). A resting quote placed
  inside a 92¢ spread captures an enormous per-fill edge *if* it gets filled by an uninformed
  crosser.
- **Why it exists / who's on the other side:** these are long-dated, low-news-frequency
  person/outcome markets. No professional market maker finds it worth quoting them tightly
  because inventory must be held for months; the flow that does arrive is retail taking a view,
  crossing whatever is showing because there is no alternative.
- **Why it persists:** it is the mirror of the exchange's tight markets — Kalshi's arbitrageurs
  concentrate where turnover is, and turnover here is episodic. The spread is wide *because*
  holding period is long, which is a genuine cost, not a free lunch.
- **Edge family:** maker / liquidity provision — the family that produced the portfolio's only
  live +EV book (`mmsell`). But the **adverse-selection prior is DANGER** (weather maker: +1¢
  gross → −8.6¢ realized), and Area 2 screened two-sided MM on informative markets as KILL. The
  distinguishing claim here is that these markets are *slow*, so the informed-flow story is
  weaker — a claim the census does not test and the follow-on probe must.

## Why a census before a thesis

The 49–117M volume figures from `kalshi_market_survey` are **cumulative-to-date**, not current
flow. Every promotable version of this idea assumes recent, repeated crossing of a wide spread.
If the volume is a historical artifact of a few episodic bursts (a primary result, a tournament
Sunday), there is nothing to provide liquidity *to*, and the idea dies for free — the same shape
as `STREAMPIN`, whose instrument existed but never traded intra-window.

## Pre-registered census gates

- **C1 — the spread is real and current, not a stale-quote artifact.** For each target series,
  sample top-of-book repeatedly over ≥ 3 days. **PASS** if the median spread on actively-quoted
  markets stays ≥ 20¢. **FAIL** if the wide average is driven by dead rungs quoted 0/100 while
  the live rungs trade tight — in which case the survey number is an averaging artifact and the
  idea is dead.
- **C2 — there is recent flow.** From `/markets/{ticker}/trades`, count trades in the trailing
  30 days per series. **PASS** if ≥ 100 trades/week land in markets whose spread at the time was
  ≥ 20¢. **FAIL** below that — no flow, no fills, no book.
- **C3 — the flow crosses a wide spread rather than trading at a tightened touch.** For each
  trade, reconstruct the prevailing quote. **PASS** if ≥ 30% of trades executed while the spread
  was ≥ 20¢. **FAIL** if crossers essentially only arrive once someone has already tightened —
  that means we would be competing at the touch, not capturing the vacuum.
- **C4 — cadence supports a readable track record.** **PASS** if ≥ 1 target series settles
  weekly-or-faster (`KXPGATOUR` is the candidate — 147 markets, weekly tournaments). **FAIL** if
  every survivor is a one-off election, which cannot produce a Kelly-sizable, statistically
  readable record regardless of per-fill edge.
- **Decision rule:** promote to a full pre-registered thesis **only if C1–C4 all pass**. If C1 or
  C2 fails, log the ruling-out and close the wide-spread pocket. If C1–C3 pass but **C4 fails**
  (i.e. the flow is real but only in lumpy election markets), record it as a **capacity-blocked
  HOLD** — real edge, unreadable cadence — and do not build. Any follow-on thesis must carry an
  explicit adverse-selection haircut and may not use gross spread capture as its headline number.

## Probe plan (census pass)

- **Script:** new `scripts/kalshi_widequote_census.py`, read-only, stdlib-only; allowlist in
  `ops_runner.py`. Reuses `kalshi_market_survey`'s pagination and the `kalshi_mm.py` trade-tape
  reader (`/markets/{ticker}/trades`) — the same tape that produced the maker-sell finding, so
  the counterparty-inference logic is already written and tested.
- **Dataset + provenance:** Kalshi public market-data API only; single provenance. The trade tape
  is the same source as `kalshi_mm`, so results are directly comparable to the maker-sell
  baseline.
- **No-lookahead:** C3 reconstructs the prevailing quote from the tape/candles **strictly before**
  each trade's timestamp. No trade may use a quote stamped at or after its own execution.
- **Measurement:** per series — median spread on quoted markets, trades/week, the fraction
  executed at ≥ 20¢ spread, settle cadence, and displayed depth at the touch.
- **Promotion result:** C1–C4 all pass → write the full thesis with an adverse-selection haircut
  and hand to `kalshi-strategy` Phase 2. Otherwise → log the verdict and close.

## Cost + capacity

- **Fee math:** Kalshi charges **maker fees too** (not rebated) — `ceil(7·p·(1−p))` cents. At the
  ~50¢ average price of `KXGOVCA`/`KXMAYORLA` that is the worst point on the curve (~2¢), so a
  wide-spread capture must clear ~2¢ before it is worth anything.
- **Adverse selection:** assumed **filled-when-wrong** until this census's own tape proves
  otherwise. The weather-maker lesson is the base case, not the exception.
- **Capacity:** the open question C2/C3 exist to answer. Cumulative volume is not capacity.

## Correlation

- **Vs current book:** shares the *mechanic* (resting quotes) with `mmsell10` but not the
  **return driver** — `mmsell` monetizes the longshot premium by selling 5–35¢ YES, whereas this
  monetizes a structural liquidity vacuum at ~50¢ in categories (Elections, golf outrights) the
  portfolio has no exposure to. PORT's family-structural clustering would need to see the probe
  results before deciding whether it collapses into the mmsell cluster; treat it as
  **provisionally independent** and re-check at promotion.
- **Value to $100/mo:** this is the run's only candidate for the **second independent +EV book**
  that PORT named as the binding constraint. That is precisely why it must clear a census before
  it gets a thesis — the failure mode to avoid is building the exciting one on cumulative volume.

## RESULTS (2026-07-26, `scripts/kalshi_widequote_census.py` via the ops channel)

**Two runs, same verdict: RULED OUT.** The census live-discovers its target series (ranked by
current avg spread, volume-floored) rather than trusting the 07-25 survey's numbers, which
already told the story before a single trade was examined:

**Run 1 — auto-discovered widest-current-spread series** (`KXGOLFMAJOR` 95.5¢, `KXNASCARCHALLENGE`
95.2¢, `KXGOVCA` 92.0¢, `KXWMARMAD1SEED` 90.9¢, `KXKENYASENATE` 89.9¢ — note none of the 07-25
survey's named candidates even made this run's top 5, itself a sign of how fast this pocket
drifts). **Every series fails C2** (recent wide-spread flow): best case `KXNASCARCHALLENGE` at
22.2 wide-spread trades/week is closest but still well under the 100/week floor, and even there
only 4% of its 2,113 sampled trades executed at a wide prior spread (C3 fail — the flow arrives
after the quote has already tightened, so a resting order here competes at the touch, not in the
vacuum). `KXGOLFMAJOR` is the one series with a real C3 signal (81% wide) but is too thin (5.8
trades/week) and too lumpy (544-day gap between distinct settle dates, C4 fail).

**Run 2 — the originally-named candidates directly** (`--series KXGOVCA,KXMAYORLA,KXPGATOUR`):
**decisive.** All three now show razor-tight CURRENT spreads — `KXGOVCA` 0.4¢ median (2
live-quoted markets), `KXMAYORLA` 1.0¢, `KXPGATOUR` 0.1¢ (66 live-quoted markets, out of 147
total) — nothing like the 92.0¢/80.2¢/55.2¢ averages `kalshi_market_survey` reported on 07-25.
**Zero wide-spread trades in any of the three, out of 3,960/4,000/20,000 total trades sampled
respectively.** This is the exact averaging artifact the census was pre-registered to catch:
the wide *average* was driven by a handful of illiquid rungs sitting at a default-quoted
0¢/100¢, while the markets that actually trade are tight. C1 and C2 both fail on all three,
cleanly, with large samples — not a close call.

**Decision: RULED OUT, no promotion, no paper book.** Per the pre-registered decision rule, a
C1-or-C2 failure closes the wide-spread pocket. The cumulative volume behind the 07-25 survey's
wide-average numbers is real (thousands to tens of thousands of trades per series) but belongs
to the *tight*, live-quoted rungs — the wide numbers were dead-rung noise, not a liquidity
vacuum with anything to provide liquidity to. **This closes PORT's "second independent book"
search down this specific avenue** — the binding constraint (edge supply, not allocation) is
unchanged.
