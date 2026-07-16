# mmsell4–8 — five pre-registered variants from the live+paper decomposition

*Written 2026-07-15, before these variants have any settled trades. The theses, specs and
promote/kill gates below are **pre-registered** so the test can't be re-scoped after the fact.
Status: **BUILT as ride-along PAPER variants (config `mmsell_variants`), inert to the live book
(only `mmsell3` is in `LIVE_STRATEGIES`).*** These extend the mmsell cohort
(`docs/RESEARCH_JOURNAL.md`, `docs/edge_research.md`); the loop checker tracks them via
`docs/BOOK_REGISTRY.md`.

## What the data showed (the motivation)

mmsell3 (yes 5–10¢ maker-sell, hold to settlement) went live 2026-07-13. By n≈163 settled its
**pooled** live P&L looked like ~breakeven (−0.3¢/trade). Decomposing it revealed that pooled
number was hiding two opposite books:

| mmsell3 live | n | win% | ¢/trade |
|---|---|---|---|
| **non-World-Cup** | 108 | **96.3%** | **+5.6¢** |
| **World Cup soccer (`KXWC*`)** | 60 | 81.7% | **−9.9¢** |

They almost exactly cancel. Two findings drive the variants:

1. **World Cup soccer is the dominant drag — structurally AND from adverse selection.** In *paper*
   (which assumes free fills) mmsell3 on WC is already −0.7¢/trade (n=269) vs +4.0¢ non-WC; live WC
   is worse still (81.7% win vs paper's 91.8%) — the ~10pp gap is real adverse selection, and it is
   **entirely concentrated in WC**. Non-WC live win% (96.3%) matches paper (96.3%) exactly — **zero
   adverse selection off World Cup.** WC props are exotic in-play soccer markets (exact score, first
   goal, goals, margin) where the favorite-longshot overpricing is weak and in-play news picks off
   the resting maker.
2. **Beyond WC, the losers cluster by sport and by market type:**
   - **Cricket** is −EV in *every* band (control −12.3¢ n=35; all variants negative).
   - **Tennis / cricket "match-winner" (1v1)** markets are the weak market *type* (mmsell3 match-
     winner −3.7¢ n=36) — sharp money prices the underdog efficiently.
   - **Totals, spreads, and discrete event-props** (All-Star, HR-Derby) are the *strongest*
     (mmsell3: total +5.5¢, spread +3.4¢, All-Star props +8.9¢, all ~98–100% win).
   - **Crypto flips by band**: cheap 5–10¢ crypto is +7.3¢, but the wider band is −8.3¢.

Prior work already established (do not re-test): the price-band decomposition (→ mmsell3 = 5–10¢),
and that **TP/SL exits hurt** (`kalshi_mm_exits.py`: hold-to-settlement wins on mean AND Sharpe;
stops whipsaw and can't catch the gap-tail). So none of these variants use exits — the risk control
stays small size + diversification.

## The five variants

Common shape unless noted: maker buy-NO in the stated yes-price band, held to settlement, same
scan/orderbook as the control. Series filters use `skip=`/`only=` (`+`-joined substrings matched
against the series prefix).

| tag | spec | thesis (what it tests) |
|---|---|---|
| **mmsell4** | `lo=5,hi=10,skip=WC+ATP+ITF+WTA+T20+ODI` | **Clean book.** The 5–10¢ edge is real but diluted by proven −EV cohorts; removing World Cup + tennis + cricket lifts per-trade to the non-WC level. The direct operationalization of the decomposition. |
| **mmsell5** | `lo=5,hi=12,only=TOTAL+SPREAD+ASG+HRDERBY` | **Market-type concentration.** The edge lives in over/under totals, spreads and event-props and is absent in head-to-head winners; a type allowlist should beat a sport-exclusion book. (Naturally excludes the bad WC *props* while keeping the fine WC totals/spreads.) |
| **mmsell6** | `lo=5,hi=8` | **FLB monotonicity at the floor.** 5–10¢ already beat 10–20¢; test whether the very cheapest longshots are *even more* overpriced, or whether the 1¢-fee floor / all-or-nothing tail caps the edge. |
| **mmsell7** | `lo=5,hi=10,htcmax=24` | **Short-dated variance test.** Multi-day cheap longshots carry more path/adverse-selection risk between fill and settle; restricting to markets settling within 24h should give a cleaner, lower-variance harvest. |
| **mmsell8** | `lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY` | **Adverse-selection isolator.** The live-vs-paper gap is caused by *in-play informed flow*; markets that settle on a scheduled time (crypto daily + event props), not a live-game event, should show ~zero live-vs-paper adverse selection. Most valuable once live. |

## Pre-registered promote / kill gates (what the loop reports on)

Evaluate each at **n ≥ 150 settled** (mmsell5/mmsell8 at **n ≥ 100** — narrower allowlists accrue
slower and mmsell8 may be sample-starved off event windows). Per-trade P&L is net of the modeled
Kalshi fee. Baseline comparators: `mmsell3` (the incumbent live book) and non-WC mmsell3 (+~5¢).

- **mmsell4 (clean book):** PROMOTE (candidate to replace mmsell3 as the live book) if per-trade
  **> +2¢ AND > mmsell3**; KILL if per-trade **< mmsell3** — i.e. removing the −EV cohorts didn't help.
- **mmsell5 (type allowlist):** PROMOTE if per-trade **> mmsell4** (type-selection beats sport-
  exclusion); KILL if **≤ mmsell3**.
- **mmsell6 (5–8¢):** PROMOTE if per-trade **> mmsell3 AND win% ≥ mmsell3**; KILL/park if **≤
  mmsell3** — the edge plateaus and 5–10¢ stays the sweet spot.
- **mmsell7 (short-dated):** PROMOTE if per-trade **≥ mmsell3 AND lower P&L stdev** (Sharpe
  improvement, even at flat mean); KILL if per-trade **< mmsell3** with no variance benefit.
- **mmsell8 (scheduled-settle):** the key read is **live win% within 1pp of paper win%** (confirms
  adverse selection is in-play-specific) AND per-trade **> +2¢**; KILL if per-trade **≤ 0** or it
  stays sample-starved (n < 50 after two weeks) → conclude the allowlist is too narrow.

**Winner action:** whichever variant clears its gate and beats mmsell3 becomes the candidate for the
next live-book config (via the `LIVE_STRATEGIES` allowlist), following the same staged-sizing +
demo-dry-run path as mmsell3 (`docs/MMSELL_LIVE_PLAN.md`). Losers are parked (config stays, book goes
dormant) with the −EV verdict recorded — a research win either way.
