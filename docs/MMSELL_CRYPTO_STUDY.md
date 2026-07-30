# mmsell crypto cheap-tail study — stop-loss, volatility gates, and the short strangle

**Question:** `KXBTCD ≤7¢` was the most promising "anchor" candidate (97.4% win, +2.76¢/trade) —
but at n=38 settled (11 with a captured tick path, **zero losers**) our own data cannot decide
anything. Can a tight stop, a volatility gate, or a two-sided strangle tame the −94¢ tail enough
to justify a larger allocation? `scripts/mmsell_crypto_study.py` (ops: **"mmsell crypto study"**)
backtests all three over Kalshi's own settled history.

## Headline: no anchor yet — but two mechanisms were validated and one was killed

| idea | verdict |
|---|---|
| catastrophic stop-loss | **mechanism VALIDATED** — improves *both* mean and tail on crypto, unlike sports. Does not make the trade +EV. |
| volatility **entry** gate | **right sign, underpowered** — calm tape +2.85 to +5.25¢ at 100% win; active tape −39¢. n=13–17. |
| volatility **exit** gate | **KILLED** — every window/threshold is far worse than holding. |
| short strangle | **most intriguing, badly underpowered** — +3.30¢/pair at 100% win, but n=23 and it fails its confidence bound. |

## The two limitations that bound everything below

**1. The window we can measure is NOT the window mmsell trades.** Kalshi only serves ~1 hour of
candle history for these series (`KXBTCD`/`KXETHD` median available span: **0.98h**), so every
backtest entry has hours-to-close **< 1h** (median 0.83h). mmsell requires `htc ≥ 1.0h`, so **it
would not have taken a single one of these trades.** The ≥1h crypto tail — the actual paper book
showing +2.76¢ — remains unmeasured, and this data cannot measure it.

**2. Effective sample is ~23 events, not 313 legs.** The 160 cheap-YES and 153 cheap-NO legs come
from only **23 distinct BTC/ETH hourly events** (~13 strikes each). Legs inside one event share the
same underlying path, so they are heavily correlated. Every statistic below has far less power than
its raw n suggests — treat `n=160` as closer to `n=23`.

Given those, the honest read of the measurable window: **the final-hour crypto cheap-tail sell is
−EV** (cheap-YES −3.67¢, cheap-NO −5.20¢, 7.5% losers vs the 2.6% our ≥1h paper book sees). This
independently reproduces **theta's** documented failure, which died selling crypto tails in exactly
this final-hour window with realized tails 1.4–2.6× modeled.

## 1) Stop-loss grid — the mechanism works here

Baseline HOLD: **−3.67¢**, 92.5% win, p5 −95.5¢. Every bid-triggered stop improves **both** metrics:

| rule | mean | p5 tail | %exit | Δmean | Δtail |
|---|---|---|---|---|---|
| **bid L15 K1** | **−0.56¢** | −21.5¢ | 18% | **+3.12** | **+74** |
| bid L12 K1 | −0.94¢ | −16.5¢ | 26% | +2.74 | +79 |
| bid L20 K1 | −1.41¢ | −31.5¢ | 15% | +2.26 | +64 |
| bid L15 K2 | −1.22¢ | −34.5¢ | 16% | +2.46 | +61 |
| bid L40 K2 | −2.64¢ | −50.5¢ | 11% | +1.04 | +45 |

This is the **opposite** of the sports result in `docs/MMSELL_EXIT_STUDY.md`, and the reason is
structural: a sports longshot jumps (a goal is scored) and cannot be stopped out, while a BTC
threshold walks continuously (6→15→30→60→100) as spot approaches the strike. **The
continuous-path thesis is confirmed.** Note it contradicts the earlier hand-derived table that
predicted a *loose* ~30¢ stop would be best — measured, the optimum is **tighter (L12–L15)**,
because crypto whipsaw is milder than the mmsell-paper touch rates implied.

Caveat: a stop turns a −EV trade into a *less* −EV trade here. It is not a rescue. Its real value
is conditional — *if* the ≥1h crypto trade is +EV, a bid-triggered L15 stop plausibly improves it.

### Methodological result worth keeping: trigger on the BID, never the mid or ask

| trigger | K=1 result |
|---|---|
| **bid** | −0.56 to −2.08¢, 11–26% exit — sane |
| mid | **−74 to −91¢, 100% exit** — pure artifact |

A previous candle backtest returned −93¢/100%-exit for every rule and was written off; this shows
why. At these prices thin books quote wide, so the **mid** is contaminated (bid 8 / ask 62 → mid 35
clears a 30¢ stop with no real buyer above 8¢). A rising **bid** is genuine buying interest. The
K=2 confirm independently repairs the mid (mid-K2 ≈ bid-K2), so *either* the bid trigger or a
≥2-tick confirm is required — the failure mode needs only one of the two guards, but never zero.

## 2) Volatility gates

**Entry gate — the right sign, too little data.** Bucketing entries by pre-entry yes-mid range:

| pre-entry range (W=15min) | n | mean | win% |
|---|---|---|---|
| calm <3¢ | 13 | **+2.85¢** | **100%** |
| mild 3–6¢ | 4 | **+5.25¢** | **100%** |
| active 6–10¢ | 9 | **−39.39¢** | 55.6% |
| wild ≥10¢ | 76 | −2.05¢ | 93.4% |

Calm entries are clearly positive and volatile ones clearly negative — the thesis's predicted
direction. But the calm buckets hold n=13–17, and the non-monotonicity (active worse than wild)
signals noise. Holds the same shape across W=15/30/60/120. **Promising, not bankable.**

**Exit gate — killed.** Every (W, V) combination lands −13.7 to −70.3¢ *below* hold, exiting
71–100% of positions. Final-hour crypto is always volatile, so a vol exit fires on nearly
everything. Consistent with the live exit study. Do not pursue.

## 3) Short strangle — the standout, and the most fragile

Pairing a cheap-YES (high strike) with a cheap-NO (low strike) in the same event. Price-based
selection forces `low_strike < high_strike`, which is what makes the legs mutually exclusive.

| | n | mean | p5 | win% |
|---|---|---|---|---|
| cheap-YES leg alone | 160 | −3.67¢ | −95.5¢ | 92.5% |
| cheap-NO leg alone | 153 | −5.20¢ | −95.5¢ | 91.5% |
| **STRANGLE (paired)** | **23** | **+3.30¢/pair** | **+2.0¢** | **100%** |

Per dollar deployed: **+3.29¢/$ paired vs −3.85¢/$ single-leg.** Both legs lose alone; paired they
win. **Both legs lost in 0/23 pairs — the exclusivity invariant held empirically.**

Why two −EV legs combine into a +EV pair: **selection.** An event where *both* tails are
simultaneously cheap is an event the market prices as low-volatility — and in this sample those
events didn't move. That is the same signal the entry vol gate found, expressed structurally.

**Why it is not yet actionable:**
- **n=23, and it fails its bound.** The pair wins +3.30¢ but loses ~−88¢ when a tail breaks, so
  break-even needs a **96.4%** win rate. With 0 losses in 23, the exact 95% lower bound is
  **87.8%** — well short. It needs roughly **82 consecutive clean pairs** to clear.
- It is a **pure short-volatility bet on a subsample selected for low volatility** — precisely the
  structure that breaks in a regime shift, and the family already has a corpse (theta) from
  under-estimating crypto tails.

## What this changes

1. **Don't allocate to a BTCD anchor.** Nothing clears its confidence bound; the only measurable
   window is −EV; and theta's failure is reproduced in that window.
2. **Keep the bid-triggered stop as validated infrastructure.** It is the first exit rule in this
   whole family that improves mean *and* tail. It belongs to continuous-path markets (crypto),
   not to jump markets (sports).
3. **Vol exit is dead. Vol entry gate is the best live-candidate lever** — cheap to implement
   (`crypto_spot_candles` already collects what's needed) and directionally supported.
4. **The strangle deserves a paper book, not capital** — it's free to accrue the ~82 pairs needed,
   and it doubles throughput under the supply constraint that currently binds mmsell.
5. **To ever validate the ≥1h crypto trade we need our own tape**, since Kalshi's candles won't
   reach back. `mmsell_position_ticks` is already capturing it — this is a data-maturity wait.
