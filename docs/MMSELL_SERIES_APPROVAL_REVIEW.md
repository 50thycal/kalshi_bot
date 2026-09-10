# mmsell series approval — the combined review, and batch 1's result

**Written 2026-09-10.** Supersedes the rules-only sign-off as the review FORMAT.
Predecessors: `docs/MMSELL_SERIES_SCORECARD_HANDOFF.md` (the score design and the fitted `k`),
`docs/SERIES_RULES_AUDIT.md` (the settlement-rules audit and batch 1's signatures),
`docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md` (the corrected contest key these numbers use).

> ## Nothing in this document gates anything.
> `registry.admits()` reads a series' `state`, and all 138 manifest rows are `graduated`. A
> **Reject** below therefore stops nothing today — the series keeps trading. Making these
> verdicts bind is a Platform Change (see §5), and the operator has deliberately deferred it.

## 1. Why the review has four parts

The first sign-off (2026-09-08, 6 of 138 rows) recorded **only** that a human had read how a
contract settles. That is one of four things worth knowing before a series is approved, and on
its own it is close to useless as a trading decision — `KXNFLSPREAD` is CONFIRMS on settlement
rules and is the worst cell in the family.

The operator's instruction, verbatim: *"for one batch we should be able to confirm is the
category right, is the rules right for how it resolves, are there any opportunities for multiple
contracts in this series to resolve the same based on one outcome and has this historically been
profitable"*. One decision per series, not four phased sign-offs.

| check | source | question it answers |
|---|---|---|
| 1. category | `mmsell/market_types.SERIES_TYPES` | is the contract TYPE recorded correctly? |
| 2. rules | `scripts/series_rules_audit.py --evidence` | does Kalshi's settlement text match the recorded mode? |
| 3. concentration | contests vs markets under the corrected key | can many contracts resolve on ONE outcome? |
| 4. profitability | `scripts/mmsell_series_pnl.py` | has its own record earned anything, and is it enough record to say? |

**Check 3 is the one that was missing**, and it is the one that changed the batch's answer.

## 2. Batch 1 — all four checks

Worklist: the ten graduated series with the most live exposure
(`series_registry_review --section backlog --top 10`). Measured all-time on settled +
`closed_sl` mmsell paper trades, twins excluded, under the **subject-split** contest key.
`own%` is `contests / (contests + 30)` at the refitted `k = 30`.

| series | category | rules | contracts / outcome | P&L | edge | contests | own% | verdict |
|---|---|---|---:|---:|---:|---:|---:|---|
| `KXNFLSPREAD` | `spread`/`in_play` ✅ | CONFIRMS ✅ | **8.66 avg, 21 max** ❌ | −$151.26 | −10.3 | 44 | 59% | **Reject / re-test capped** |
| `KXMLBSPREAD` | `spread`/`in_play` ✅ | CONFIRMS ✅ | 4.15 avg, 11 max ⚠ | +$130.10 | +2.6 | 373 | 93% | **Approve at cap** |
| `KXRAIN` | `event_stat`/`scheduled` ✅ | overrule ✅ | **1.00 — none** ✅ | +$68.65 | +9.8 | 222 | 88% | **Approve** |
| `KXTRUMPSAY` | `mention`/`discrete` ✅ | overrule ✅ | **1.00 — none** ✅ | +$61.85 | +10.8 | 119 | 80% | **Approve** |
| `KXWTI` | `price_strike`/`scheduled` ✅ | unread ⚠ | **8.39 avg, 18 max** ❌ | −$61.16 | −9.2 | 18 | 38% | **Reject** |
| `KXMLBHR` | `player_prop`/`in_play` ✅ | CONFIRMS ✅ | **6.63 avg, 21 max** ❌ | +$54.68 | **+0.6** | 317 | 91% | **Drop or cap** |
| `KXALBUMEQUIV` | `rank_culture`/`discrete` ⚠ | unread ⚠ | 4.27 avg, 8 max ⚠ | −$51.13 | **−19.5** | 11 | 27% | **Reject** |
| `KXNFLTOTAL` | `total`/`in_play` ✅ | CONFIRMS ✅ | **7.00 avg, 13 max** ❌ | +$49.16 | +6.1 | 43 | 59% | **Approve at cap** |
| `KXITFMATCH` | `h2h`/`in_play` ✅ | CONFIRMS ✅ | 1.24 avg, 2 max ✅ | +$42.55 | +2.6 | 654 | 96% | **Approve** |
| `KXINXU` | `price_strike`/`scheduled` ✅ | CONFIRMS ✅ | 6.00 avg, 19 max ⚠ | +$33.58 | +7.4 | 17 | 36% | **Approve at cap** |

## 3. The finding: the book routinely holds 4–9 contracts on one outcome

Up to **21**. One NFL game carried 21 `KXNFLSPREAD` markets; one MLB game carried 21 `KXMLBHR`
markets; one oil print carried 18 `KXWTI` strikes. The book's entire premise is diversification,
and on these series it does not have any: 21 contracts on one game is one position at 21× size.

It compounds across series. An NFL game is shared with ~3.6 other traded series on average
(max 8); an MLB game with ~5.4 (max 13). One Sunday afternoon moves 20+ positions the risk model
counts as independent. That is the XOS-000020 finding, now measured per series.

**What this does and does not prove.** For `KXNFLSPREAD`, `KXMLBHR`, `KXMLBSPREAD`, `KXWTI`,
`KXALBUMEQUIV` and `KXNFLTOTAL`, 99–100% of all money lost came from multi-contract outcomes —
but ~100% of their contests ARE multi-contract, so that share is tautological and carries no
causal weight. The one row with a real contrast is `KXITFMATCH`: 24% of its contests are
multi-contract and they carry 45% of its losses, ~2.5× over-represented. Real, and small.

**The honest claim is that concentration makes the losses fat-tailed, not that it causes them.**

### Two caveats on the cross-series numbers

`KXRAIN` (5.93 series per event) and `KXTRUMPSAY` (7.50) look heavily shared and are not: their
event token is a bare date (`26SEP06`), so they collide with every other series that keys on a
date. Spurious. For the sports series the token is a real game (`26AUG13ARILV`) and the figures
are genuine. `KXWTI` (1.22) and `KXINXU` (2.24) key on date+hour — partially spurious, low enough
not to matter.

## 4. What the verdicts mean, and the decision taken

`KXRAIN`, `KXTRUMPSAY` and `KXITFMATCH` are clean on all four and are the only three in the batch
with no meaningful concentration.

`KXNFLSPREAD` is the interesting reject. It is not obviously a bad **selection** — it is a book
holding 8.66 contracts per football game and losing when a game goes one way. Capped at one per
game it might be fine. **We have never run it capped, so that is not a claim, it is the next
measurement.**

`KXMLBHR` is the marginal one: +$54 but edge **+0.6pp on 317 contests**, which is a well-measured
near-zero carrying the batch's worst concentration.

**Decision, 2026-09-10: fix the concentration first, do not gate.** The concentration lever is
already built and already measured (`Gmmsell1` / `Gmmsell2`, `docs/MMSELL_CORRELATION_CAP.md`),
and it is scoped the way the operator asked for — **a fresh paper tape on its own tag**. It
touches no existing paper book and no live book, so its effect is readable on its own. If capping
does not fix the P&L, gating is worth building then.

## 5. Why gating was deferred, and what it would cost

Making these verdicts bind means adding them to the manifest row schema and having
`registry.admits()` require approval. That is a **Platform Change Review**: `admits()` is shared
by every mmsell book including the live canary's universe, so it moves them all at once, which
under `NEW_ONLY` is an epoch or Version decision rather than a config change.

It only ever REMOVES series, so it moves exposure in the safe direction — but it is still a
universe change to running books, and the operator's stated preference is to measure one change
at a time on an isolated tape rather than move every book together. Recorded as deferred, not
rejected.

## 6. What batch 1's six signatures actually mean

`KXMLBSPREAD`, `KXNFLTOTAL`, `KXITFMATCH`, `KXINXU`, `KXRAIN`, `KXTRUMPSAY` carry
`rules_reviewed_at: 2026-09-08` / `rules_reviewed_by: 50cal`. That records **check 2 only** — a
person read how those six settle. It is not an approval to trade, it gates nothing, and the four
series this document rejects or flags are unaffected by it. Reading the signature as a trading
approval is the specific misunderstanding this document exists to prevent.

## 8. Batch 2 — measured 2026-09-10, verdicts PROPOSED (not signed)

Backlog ranks 11–20 by live exposure; ranks 1–10 were batch 1 and the four it did not sign
(`KXNFLSPREAD`, `KXMLBHR`, `KXWTI`, `KXALBUMEQUIV`) still head the list with their §2 verdicts.
Audit: `CONFIRMS=4 CONTRADICTS=1 INSUFFICIENT=5`.

| series | category | rules | contracts / outcome | P&L | edge | contests | own% | proposed |
|---|---|---|---:|---:|---:|---:|---:|---|
| `KXWTAMATCH` | `h2h`/`in_play` ✅ | CONFIRMS ✅ | 1.28 avg, 2 max ✅ | +$22.66 | +3.1 | 307 | 91% | **Approve** |
| `KXWNBASPREAD` | `spread`/`in_play` ✅ | CONFIRMS ✅ | 5.70 avg, 10 max ⚠ | +$33.16 | +2.6 | 56 | 65% | Approve at cap |
| `KXNASDAQ100U` | `price_strike`/`scheduled` ✅ | CONFIRMS ✅ | **7.77 avg, 23 max** ❌ | +$25.38 | +10.6 | 13 | 30% | Approve at cap |
| `KXNATGASD` | `price_strike`/`scheduled` ✅ | CONFIRMS ✅ | **10.22 avg, 30 max** ❌ | +$27.12 | *none yet* | 9 | 23% | Approve at cap, flagged |
| `KXYTVIEWSW` | `event_stat`/`discrete` ❌ | **CONTRADICTS** | 2.76 avg, 7 max ⚠ | +$23.84 | *none yet* | 17 | 36% | Fix category first |
| `KXBTC` | `price_strike`/`scheduled` ✅ | unread ⚠ | **9.85 avg, 25 max** ❌ | +$11.83 | +1.6 | 13 | 30% | Cap |
| `KXBTCD` | `price_strike`/`scheduled` ✅ | unread ⚠ | **8.05 avg, 19 max** ❌ | −$19.84 | **−0.7** | 60 | 67% | Cap or drop |
| `KXETHD` | `price_strike`/`scheduled` ✅ | unread ⚠ | 3.77 avg, 12 max ⚠ | −$18.00 | −4.3 | 22 | 42% | Reject |
| `KXAAAGASD` | `price_strike`/`scheduled` ✅ | unread ⚠ | 2.96 avg, 6 max ⚠ | −$33.30 | −11.7 | 24 | 44% | Reject |
| `KXTRUTHSOCIAL` | `mention`/`discrete` ❌ | unread ⚠ | 5.20 avg, 8 max ⚠ | −$20.57 | −10.6 | 5 | 14% | Reject + fix category |

### Two taxonomy defects — OPEN, and neither is fixed here

Changing `SERIES_TYPES` is shared platform semantics: a **Platform Change Review**, never a
side effect of a review batch.

**`KXYTVIEWSW` — recorded `discrete`, should be `in_play`.** The audit's first CONTRADICTS in
either batch, and it is correct. *"If Future has above 5.5M Global daily views on YouTube **at any
point during** September 7 – September 13, then the market resolves to Yes."* A one-way trigger
live for a whole week is not a discrete event.

**`KXTRUTHSOCIAL` — recorded `mention`/`discrete`, should be a threshold on `scheduled`.** The
audit returned INSUFFICIENT; the regex missed it and the text does not. *"If the number of Truth
Social posts by Donald Trump from Aug 30 to Sep 5 is below 80…"* / *"above 240…"*, from Roll Call,
**recorded at 10:00 AM ET on Sep 6**. A threshold ladder on one weekly count from a named source
at a named instant. Both halves of the recorded category are wrong. Predicted while building
`SUBJECT_SPLIT_SERIES` (`docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md` calls `KXTRUTHSOCIAL` a
threshold series typed `mention`); now confirmed from Kalshi's own settlement text.

### Two corrections to these numbers, stated so nobody reads them wrong

**`KXNATGASD` and `KXYTVIEWSW` have no edge figure, not a spectacular one.** The raw query
returned "+100.0" for both. That is an artifact: break-even is `avg_win/(avg_win+|avg_loss|)`, and
with **zero losing trades** the denominator collapses. The honest reading is *no losses yet* on
350 and 193 trades. Unproven, not exceptional.

**The cross-series column is meaningless for the six `price_strike` series.** Their event token is
a date or date+hour, so it collides with everything date-keyed — and an hour token merges `KXBTCD`
with `KXETHD`, different underlyings under one key. Reported as `date?`, never as a number.

### What batch 2 changes about the thesis

**Only one series of ten is clean on all four**, against three in batch 1.

Batch 1 made the concentration look like a sports problem. It is not — it is every ladder-shaped
market, and the commodity/crypto ladders are the deepest measured anywhere: `KXNATGASD` averages
**10.22 contracts per gas print and peaked at 30**, `KXBTC` at 25, `KXNASDAQ100U` at 23, `KXBTCD`
at 19. `KXBTCD` is the batch's volume monster — **2,921 trades to reach an edge of −0.7**, with
−$200.47 of gross losses in multi-contract prints against −$0.82 in single-contract ones (98% of
its contests are multi-contract, so read that pair per §3's warning).

`KXNATGASD` is the one to watch: the largest single-outcome exposure in the registry, and it has
never yet been tested by a loss.

## 7. Reproducing

```
{"type":"script","name":"series_registry_review","args":["--section","backlog","--top","10"]}
{"type":"script","name":"series_rules_audit","args":["--only","<batch>","--evidence"]}
{"type":"script","name":"mmsell_series_pnl","args":["--all-time","--split-subjects"]}
```

```
{"type":"script","name":"series_concentration"}
{"type":"script","name":"series_concentration","args":["--only","KXBTC,KXETHD"]}
```

Check 3 now has a script (`scripts/series_concentration.py`, added 2026-09-10). Batch 1's and
batch 2's concentration numbers predate it and came from an ad-hoc `db` query of the same shape;
from batch 3 they are reproducible by name. The script defaults to the CORRECTED contest key —
the opposite of `mmsell_series_pnl`, deliberately, because there the shipped meaning must not move
under an unsuspecting reader while here an honest independence unit IS the measurement.
