# mmsell3 live trading — postmortem and wind-down (2026-07-19)

**Decision: pause new mmsell3 live entries and close out all open live positions.** The paper book
(`mmsell3` and its variants `mmsell1-11`) continues unchanged — only the *real-money* test is
ending. This doc is the record of why, the final numbers, and what carries forward.

## Timeline (compressed)

- **2026-07-12/13** — `mmsell3` (maker buy-NO, yes 5–10¢ band, hold to settlement) passed its
  paper gate (n≥150, +1.5¢/trade, ≥ siblings) and was built out as a live path
  (`docs/MMSELL_LIVE_PLAN.md`): `LiveExecutor.mirror_mmsell_entry`, a resting maker order.
- **2026-07-13** — first live orders all **rejected (410)** — Kalshi had deprecated
  `POST /portfolio/orders`. Migrated to the current `POST /portfolio/events/orders` (V2) endpoint,
  verified against recorded live request/response fixtures before touching real money again.
- **2026-07-13 to ~16** — live fills started landing; pooled live P&L drifted around breakeven,
  swinging positive and negative across small samples (expected noise at n<50).
- **~2026-07-16** — at n≈163, live win rate sat right at the plan's 90% pass/fail line and pooled
  P&L was negative. Decomposition (`docs/MMSELL_VARIANTS_THESIS.md`) found the pooled number was
  hiding two opposite books: **non-World-Cup +5.6¢/trade (96% win)** canceled by **World Cup soccer
  −9.9¢/trade (82% win)** — WC was −EV in paper too (not just an adverse-selection artifact), and
  its adverse selection was entirely concentrated there (non-WC live win% matched paper exactly).
  Shipped 5 paper variants (`mmsell4-8`) to isolate the mechanism.
- **2026-07-18/19** — World Cup faded but the **live pooled book kept drifting negative**. A second
  decomposition at n=232 found the loss engine hadn't gone away — it changed *sport* (MLB
  game-winners, tennis, cricket, esports) but not *type*: head-to-head "who wins" markets stayed
  the drag, and a clean **price gradient** appeared (yes ≤7¢ wins, ≥8¢ loses, worst at 9¢). The
  two levers stacked additively in a 2×2. Shipped 3 more paper variants (`mmsell9-11`, plus a new
  `maxyes` entry-price-ceiling knob) to test fixes.
- **2026-07-19** — before those variants could accrue a sample, the **existing paper cohort itself
  reversed hard** (mmsell5, the standout 100%-win performer across 4 consecutive checks, dropped to
  85% win / −6.2¢/trade in one update) and the live book's decline continued into a fresh MLB-totals
  slice that had previously been one of the *strongest* categories. **Decision: stop paying real
  money to keep characterizing a moving target — pause live, close positions, let the paper cohort
  (now 11 books deep) keep iterating for free.**

## Final numbers (live, at closeout)

| metric | value |
|---|---|
| live orders placed | 452 (304 filled, 139 canceled/timeout, 9 rejected) |
| fill rate | 68.6% |
| settled round-trips | 296 |
| live win rate | 89.9% (paper over the same window: 93.3%) |
| live P&L/contract | **−1.23¢** (paper: +0.80¢) |
| realized P&L (approx.) | **≈ −$3.64** |
| fees | negligible (~0.0¢/contract net — fee modeling was accurate throughout) |
| rejections beyond the 5 pre-V2-fix ones | 4, all benign `post_only cross` (the safeguard working — the resting price moved between snapshot and order arrival; no fill, no cost, not a bug) |
| open positions closed out | 10 (~$8.50) via the new `close_mmsell_positions` one-shot exit |

**Bottom line: the live test cost about $4, all in.** It never came close to threatening the ~$150
Stage-1 bankroll or the caps. The value was informational, not financial — and it delivered.

## What we confirmed works (keep doing this)

- **The V2 order-endpoint migration is correct and durable.** 304 real fills, accurate fee
  modeling, clean reconcile (fills/positions/settlements all tied out), and the `post_only`
  maker-safety flag did exactly its job on every rejection after the initial migration.
- **Fill mechanics are NOT the problem.** 68.6% fill rate is healthy for a resting maker order;
  live win rate tracked paper closely whenever the book was scoped to the right cells (non-WC:
  96.3% live vs 96.3% paper, an exact match — zero adverse selection off World Cup).
- **The paper→live pipeline (dedup, exposure caps, audit trail via `risk_events`, the scorecard)
  all worked as designed** — nothing about the *infrastructure* failed; the *strategy* needed more
  refinement than a single live test window could deliver economically.

## Lessons learned

1. **Pooled P&L on a mixed-cohort book is actively misleading.** mmsell3 traded four structurally
   different cells (cheap/rich × winner/non-winner) with opposite signs; the pooled number looked
   like "a weak edge" when it was actually "a real edge diluted by three −EV cells." Any book that
   spans multiple market types/sports needs a by-cell decomposition before its pooled number is
   trusted, live or paper — a lesson `docs/edge_research.md`'s methodology notes already half-said
   ("measure event-conditional, not averages") but this is a concrete, expensive-if-ignored
   instance of it.
2. **A loss *pattern* can survive its most visible cause disappearing.** World Cup ending didn't
   fix the live book — the same structural weakness (head-to-head winners, richer entries) simply
   reattached to different sports (MLB, tennis, cricket, esports). The lesson generalizes past
   mmsell: don't declare a fix validated just because the headline offender is gone; re-check the
   underlying mechanism.
3. **A "clean" paper signal can be small-n luck even at n=20-30, not just n<10.** mmsell5 held
   100% win for 4 consecutive checks (n=17→23→27) — looked like real separation — then reversed in
   one update once World Cup totals/spreads (which its type-only filter didn't exclude) accumulated
   enough trades to matter. The negatively-skewed shape of this whole family (mostly small wins,
   occasional near-full-stake losses) means "no losses yet" is weak evidence for longer than
   intuition suggests; a handful of new trades can flip an average that looked stable.
4. **Live vs. paper P&L windows must be point-in-time matched.** The scorecard already does this
   correctly (it computes paper stats over the SAME settled window as live), but it's worth
   restating: comparing live-to-date against paper's full history would have overstated the gap.
5. **The order-placement guard doesn't distinguish "close" from "open."** `KILL_SWITCH=true`
   blocks all real orders unconditionally, including an exit. Winding down a live book safely
   needs the entry allowlist (`LIVE_STRATEGIES`) as the "stop new trades" lever and the kill
   switch reserved for "stop everything, including closes" — see `docs/MMSELL_LIVE_PLAN.md` §9 for
   the sequence this postmortem's closeout followed.
6. **There was never a mechanism to exit a live mmsell position early — by design (the exit-rule
   study proved TP/SL hurt) — but "hold to settlement" and "wind down the whole experiment" are
   different problems.** `LiveExecutor.close_mmsell_positions` fills that specific gap: a one-shot,
   clearly-tagged, fail-soft-per-position closer, independent of (and not a replacement for) the
   entry-side TP/SL decision, which remains correctly off.

## What carries forward

- **Paper mmsell3 and all 11 variants (`mmsell1-11`) keep running** — free, no bankroll at risk,
  and now with a much better-specified hypothesis set than when live started (price ceiling, type
  allowlist, no-late-entry, and combinations thereof). The loop checker keeps tracking them against
  their pre-registered gates in `docs/BOOK_REGISTRY.md`.
- **Re-live-testing is on the table again once a paper variant clears its gate cleanly** — same
  staged path as before (demo dry-run → Stage 1 tiny size → decide), now informed by exactly which
  cells to trade instead of trading the whole cohort and decomposing after the fact.
- The V2 migration, the closeout mechanism, and the by-cell decomposition method are all reusable
  infrastructure for whichever mmsell variant (or future book) goes live next.
