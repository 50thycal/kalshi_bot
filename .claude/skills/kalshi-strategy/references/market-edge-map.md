# Kalshi Market Edge Map

Read this in Phase 1 to choose a market family. The purpose is to reason honestly about **where a solo quant with modest capital realistically has edge** — and where you'd be donating to desks, insiders, or an efficient crowd. Picking the right pond matters more than technique.

## The one empirical fact that shapes everything

Across every finalized Kalshi weather market, prices are **well-calibrated in the tails**: contracts priced 90–100% resolved YES ~98–99% of the time; contracts priced 0–10% resolved YES ~1% of the time. Mid-range probabilities are noisier. This generalizes as a working prior across categories:

- **The obvious extremes are (mostly) efficient.** Don't expect free money buying 95¢ near-certainties or fading 3¢ longshots — the crowd prices those well, and fees plus spread eat the thin edge.
- **Edge concentrates in three places:**
  1. **Mid-probability brackets** (roughly 20–80%), where the market is least sure and small information advantages move expected value most.
  2. **The time dimension** — how fast a market converges to its resolution as information arrives. Being early with a better estimate, or trading the convergence, is often the real edge.
  3. **Categories where you can compute or source better information than the marginal trader** — this is the whole game. If you have no informational or modeling advantage over whoever is setting the price, you have no edge regardless of tooling.

## What "edge" requires (test every candidate against this)

A credible thesis needs all four:
1. **An information or modeling advantage** you can actually obtain point-in-time.
2. **A reason the mispricing persists** — name who is on the other side and why they haven't corrected it (retail inattention, no rigorous model, structural constraints, slow reaction).
3. **Enough liquidity** to deploy meaningful size — a 60% hit rate on markets that absorb $50 is a hobby, not income.
4. **Costs cleared** — edge must survive fees (quadratic, worst near 50¢) and realistic fills (you cross the spread).

---

## Category-by-category

### Weather / daily temperature — **strongest fit for a solo quant** ✅
- **Why edge can exist:** settlement is a fully-specified physical quantity (the NWS Daily Climate Report high/low at a named station). NOAA publishes **probabilistic** model guidance (the National Blend of Models emits percentile and probability-of-exceedance temperature products) that maps almost directly onto Kalshi's bracket structure — a principled probability estimate that many discretionary traders eyeball rather than compute rigorously. Data is free and public. Outcomes are frequent (daily, many cities) → lots of independent bets → statistically meaningful sample for calibration and Kelly sizing.
- **Who you're beating:** other retail/discretionary weather traders reading raw model runs without a calibrated bracket distribution. You are *not* fighting insiders (there are none for tomorrow's temperature) or fast desks (edge is in the estimate, not the microsecond).
- **Watch-outs:** liquidity varies by city/day; settlement gotchas (station mapping, local-standard-time high windows) matter; strong summer/winter regime differences. Full treatment in `weather-strategy.md`.
- **Capacity:** modest but real, and scalable across ~20 cities × high/low × daily.

### Economics / interest rates (CPI, Fed decisions, jobs) — hard ⚠️
- **Why hard:** these are the most-analyzed numbers in finance. You're trading against professional macro desks and an efficient crowd; the marginal trader is often sharper than you. The path (market drifting toward a consensus) can offer some structure, but sustained edge is unlikely without a genuine analytical advantage over Wall Street.
- **When it might work:** short-lived reaction windows where the market is slow to reprice a released figure — but that's a latency/execution game.

### Crypto price levels — hard ⚠️
- **Why hard:** the underlying (BTC/ETH spot) trades in deep, fast, near-efficient markets 24/7. Kalshi price-level contracts are largely a repackaging of that; edge would come from a spot-price forecast you almost certainly don't have. Occasionally the Kalshi contract lags the underlying — that's an arbitrage/latency play, not a forecasting edge, and it's contested.

### Sports — mixed, mostly hard ⚠️
- **Why hard:** mature, sharp betting-market ecosystem (sportsbooks, exchanges) with fast lines. Kalshi prices often track those. Edge requires either a better model than the sports-betting market (a very high bar) or catching Kalshi lagging the consensus line (latency/arb). Live in-game markets add microstructure complexity.

### Politics / elections — hard and lumpy ⚠️
- **Why hard:** heavily traded, headline-driven, and event outcomes are lumpy (few independent bets, so hard to build a calibrated track record or size with Kelly). Sentiment and narrative dominate; a solo modeling edge is elusive and the variance is brutal.

### Company / tech / misc. event markets — case by case 🔍
- Grab-bag: earnings, product launches, approvals, records. **Occasionally** a niche market is thin and under-analyzed enough that a specific informational edge exists (you happen to model this domain well). Evaluate individually against the four-part edge test — don't assume.

---

## Decision heuristic

Rank candidates by:
1. **Do you have a real information/modeling advantage** you can obtain point-in-time? (No → skip.)
2. **Are outcomes frequent** enough to build a calibrated, Kelly-sizable track record? (Frequent daily events ≫ one-off lumpy events.)
3. **Is there enough liquidity** for meaningful size?
4. **Does the edge survive costs?**

For most solo quants starting from modest capital, **weather/temperature markets score highest** on all four — hence the worked example in `weather-strategy.md`. If a different category clears the four-part test for *your* specific advantage, pursue it — but be ruthless about the "why does the mispricing persist and who am I beating" question. If you can't answer it crisply, you don't have an edge yet.
