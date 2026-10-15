# What made the live canaries win or lose — three checks plus a mentions forward read

Research Lab, 2026-10-15. Pre-registration, written after reading the LIVE canary data and
BEFORE reading the paper history that tests it. Experiment OS is canonical for experiment state.
This document authorizes nothing: real money changes only through a Live Ops canary on its own
Version.

## 1. What the live data showed (ops `live-canary-markets-20261015a`)

- **Sample:** 2,870 settled, uncontested live markets across every mmsell live canary.
- **Total:** −$24.51, −0.70¢/contract.
- **Categories positive in both halves of the history** (split 2026-08-17): weather (+3.8¢),
  index (+4.2¢), and the Politics regime (+5.5¢).
- **Negative in both halves:** crypto (−8.3¢), sports totals (−2.4¢), sports spreads (−2.0¢).
- **Day of week:** no reliable effect (about 11 event days per weekday). Weekend events were
  slightly positive.
- **Losses cluster by day:** 28 event days with three or more losses lost $74.55; the other 52
  days made $48.91. This is not explained by how many positions a day held.
- **Mentions:** only 16 live markets (+$0.62).
- **Timing:** fills 12–24h before close were the worst (−5.6¢); fills under 6h before close
  were positive (small n).

These are hypotheses generated from live data. Every check below runs on **paper** history, and
on **markets the live canaries never traded**, so the evidence is independent of what produced
the hypothesis.

## 2. Data and shared rules

- **Sample:** `mmsell10` paper trades (the live rule's paper control), settled, excluding every
  market ticker that any live canary traded.
- **Category:** the fixed mapping in `scripts/live_canary_patterns.py` (`category_of`), identical
  to the one used on live. Politics is the `kalshi_bot.mmsell.regimes` regime.
- **Event date:** parsed from the ticker; close time from `mmsell_settlement_meta`.
- **Uncertainty:** a 90% interval from a bootstrap that resamples whole event days, because
  markets on one day are not independent.
- **Bar (all three checks):** an effect counts only if it is at least the stated size **and** its
  90% interval excludes zero. Otherwise it is NO EFFECT, recorded and not retried without a new
  pre-registration.

## 3. Check 1 — trade live only where we win

- **WIN set:** weather, index, Politics regime, mentions.
- **LOSE set:** crypto, sports totals, sports spreads.
- **PASS needs both:**
  - WIN-set ≥ +2.0¢/contract with its interval above zero;
  - WIN minus LOSE ≥ +2.0¢ with its interval above zero.
- **Consequence of PASS:** a proposal for a successor live canary allowlisted to the WIN set,
  as a Live Ops hard stop that needs the owner's confirmation. Nothing is armed from here.

## 4. Check 2 — a bad-day brake

- **Rule tested:** after a paper `mmsell10` loss is realized (closed), do entries made in the
  next 6 hours do worse than all other entries?
- **PASS:** post-loss entries are worse by ≥ 2.0¢/contract, with the interval of the
  difference below zero.
- **Consequence of PASS:** a proposal for a paper arm with an entry pause after a realized loss.

## 5. Check 3 — sports entry timing

- **Scope:** sports categories (totals, spreads, games, tennis/match).
- **Buckets:** c/ct by hours from entry to close: < 6, 6–12, 12–24, 24–48, > 48. Markets whose
  close is more than 336h after entry are dropped, because their close time is a far-future
  fallback, not the event.
- **Hypothesis from live:** 12–24h is worst.
- **PASS:** dropping the 12–24h bucket improves sports c/ct by ≥ 1.0¢, with the interval of
  the 12–24h bucket's difference from the rest below zero.
- **Consequence of PASS:** a proposal for a paper arm that skips that window on sports.

## 6. Mentions — a forward read on the paper already running

- **No new history test.** §15 of docs/MMSELL_LOOKALIKE_PROBE.md closed that at HOLD.
- **Forward read:** every `mmsell10` paper mention trade that **closes after 2026-10-15** counts.
- **At 60 such settled trades:**
  - ≥ +2.0¢/contract → mentions qualify for check 1's live allowlist;
  - ≤ 0¢ → mentions are dropped from it;
  - in between → HOLD until 120.
- Nothing in the existing books changes while it accrues.

## 7. Results (2026-10-15; ops `paper-mmsell10-nolive-20261015a`, `scripts/live_canary_patterns.py`)

**Sample.** 8,351 settled `mmsell10` paper trades on markets no live canary traded, over 81 event
days. All trades: +1.12¢/contract, 90% interval [+0.77, +1.47].

| check | result | reading |
|---|---|---|
| 1. WIN set (weather, index, Politics, mentions) | WIN n=277: **+4.67¢** [+3.10, +6.09]. LOSE n=3,613: +1.13¢ [+0.46, +1.77]. **WIN − LOSE +3.53¢ [+1.81, +5.14]** | **PASS** |
| 2. Bad-day brake (entries ≤ 6h after a realized loss) | after − others **+0.81¢** [−0.10, +1.74] | **NO EFFECT** (post-loss entries were not worse) |
| 3. Sports 12–24h window | 4,305 sports entries; only 3 fall in 12–24h | **NOT MEASURABLE** |

**Category detail (paper, independent markets):**
- index +4.77¢, weather +4.64¢, mentions +4.53¢: every interval is above zero;
- sports totals +1.94¢, tennis/match +2.22¢, sports games +1.44¢;
- sports spreads +0.45¢ and crypto +0.26¢: both intervals include zero.

**Readings.**
- **Check 1** passes on evidence independent of the live data that suggested it. The WIN
  categories are positive in paper (+4.7¢) **and** in live (weather +3.8¢, index +4.2¢,
  Politics +5.5¢, mentions +3.0¢ on 16).
- **The new finding is the paper-to-live gap.** Sports totals earn +1.9¢ on paper and −2.4¢ live.
  Crypto earns +0.3¢ on paper and −8.3¢ live. The fill gap is concentrated in liquid sports and
  crypto, where informed flow is plausible, and is small where the WIN categories trade. That is
  a mechanism, and it is consistent with the +2¢ average maker gap
  (docs/MMSELL_FILL_MODEL.md) being an average of two very different populations.
- **Check 2:** no evidence that losses predict further losses within six hours. No brake is
  proposed.
- **Check 3** cannot be answered from this data. For sports, `close_time` is a far-future
  fallback rather than game time, so `mmsell10`'s sports entries almost never sit 12–24h before
  it. The live 12–24h result (115 markets) is therefore unexplained, not refuted. A real test
  needs the expected-expiration time, which paper rows do not carry. This is recorded, not
  proposed.
- **Mentions forward read:** 3 of 60 so far (+6.6¢).

**Consequence of check 1, a Live Ops proposal.** This is a hard stop: it needs the owner's
explicit confirmation, and nothing is armed from here.
- **What:** a successor to the running live canary, restricted to the WIN categories.
- **Scale:** WIN-set flow is small, about 3–4 qualifying paper markets a day on markets live has
  not touched. At +4.7¢ that is about $5/month per contract of size, or about $15/month at the
  3-lot used by `Kmmsell10`.
- **What it buys:** mainly it stops the live book from paying the sports and crypto fill gap. It
  is not a route to $100/month on its own.

**Correction (2026-10-15, docs/MMSELL_EVENT_TIMING.md §6.4).**
- The live "fills 12–24h before close" cell in §1 (115 markets, −5.6¢) contains only 2 sports
  markets.
- It is mostly crypto (44 markets, −13.2¢), plus oil, gas and other. Weather is positive in it.
- It is not evidence about sports entry timing.
- Measured against the real game start, sports timing shows **NO SIGNAL**.
