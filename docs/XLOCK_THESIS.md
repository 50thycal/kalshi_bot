# XLOCK — locked arbitrage in the pockets the within-event scanner cannot see

*Thesis written 2026-07-25, before any validation ran; the falsifiable predictions below are
pre-registered. Status: pending probe.*

*Correction (2026-07-25, before the probe script's first run — not a re-scope after results):
P2's direction was wrong in the original draft below (it read `touch_yes_bid − terminal_yes_ask`).
Re-deriving the payoff table state-by-state while writing `scripts/kalshi_xlock.py` showed that
construction has an uncapped loss branch (touch settles YES on a pull-back after touching K,
while terminal settles NO — the short touch leg then pays out $1 with nothing offsetting it), so
it is a directional bet, not a lock. The only riskless construction is
`terminal_yes_bid − touch_yes_ask` (sell terminal, buy touch), corrected throughout P2 below.*

## One-liner

Kalshi's no-arb scanner is **within-event only**, so three classes of multi-leg logical
containment — parlay-vs-leg, touch-vs-terminal, and unflagged candidate sets — have never been
checked; XLOCK scans them for a genuine risk-free lock and, failing that, measures how much of the
board the standing monitor actually covers.

## Mechanism

- **What mispricing:** a market that is a **logical subset** of another trading *above* it.
  If event `P` implies event `A` (`P ⊆ A`), then `P(P) ≤ P(A)` must hold. A quote where
  `P.yes_bid > A.yes_ask` is a locked vertical: sell `P`, buy `A`, and the worst case is a
  credit. This is the same math as the existing `MONO-VERTICAL` check, applied **across event
  boundaries** rather than within one ladder.
- **Why it exists / who's on the other side:** for the parlay leg (P1), the counterparty is
  **retail parlay flow** — the single best-documented bias in sports betting is that bettors
  systematically overpay for correlated multi-leg tickets and underweight how fast joint
  probability decays. That bias pushes the parlay's *bid* up, which is exactly the direction
  that opens the lock. For the touch leg (P2), the counterparty is a trader pricing "will it
  ever touch K" and "will it close above K" in separate books without enforcing the American-vs-
  European ordering between them.
- **Why it persists:** nothing here is visible to a single-event scanner, human or automated.
  Kalshi's own UI groups by event, so the two legs of a cross-series violation never appear on
  one screen. Arbitrageurs who *are* watching (and the 0.97–0.99 tightness says they exist) are
  watching within-ladder.
- **Edge family:** structural / locked arb — a family that is **0-for-2** (DECAY, FEDRV) with a
  **LOW prior**. The specific, material difference that justifies re-opening it: these pockets
  are excluded by the existing scan's *code path* (a `continue` on `KXMVE`, a function scoped to
  one event, a silent `None` return on unflagged sets), not by a null result. See
  `docs/IDEA_MODEL_20260725_ARB.md`.

## Pre-registered predictions (each with a kill criterion)

- **P1 — Parlay containment.** Among `KXMVE*` (and any other multi-leg/combo series, discovered
  structurally rather than assumed to be only `KXMVE`) whose component single markets are
  separately listed and quotable, at least one pair shows
  `combo_yes_bid − leg_yes_ask − fee(combo_bid) − fee(leg_ask) > 0`.
  **PASS** if ≥ 1 such lock is found **and** it survives the fillability audit below (both legs
  quoted with ≥ 10 contracts of displayed size, both markets open, close times consistent).
  **KILL** if 0 locks across ≥ 200 matched parlay↔leg pairs, or if the median gap is ≤ −3¢
  (i.e. parlays trade *below* their legs, the opposite of the retail-overpricing prediction).
  **HOLD (testability-thin)** if fewer than 200 pairs can be matched at all — the component-
  discovery matcher (rules-text decomposition, see Probe plan) may simply be too strict for how
  Kalshi phrases combo rules; that is not proof combos don't exist, only that this matcher
  couldn't confirm them.
- **P2 — Touch-vs-terminal containment.** Scoped to **Crypto** in v1 (the only category with a
  confirmed `MAX`/`MIN` touch-series naming convention on the live board; a category without a
  known touch-style series would need guessed classification, which this family doesn't do). For
  every (underlying, strike K, horizon T) where a "max/touch ≥ K by T" market and a "level ≥ K at
  T" market both exist,
  `terminal_yes_bid − touch_yes_ask − fees > 0` for at least one pair — the only riskless
  direction; see the correction note above the one-liner for why the reverse construction
  (`touch_yes_bid − terminal_yes_ask`) is a directional bet, not a lock.
  **PASS** if ≥ 1 lock survives the same fillability audit. **KILL** if 0 across all matched
  pairs, or if fewer than 20 pairs can be matched on a *real identifier* (ticker-derived strike,
  never a title-parsed number — the logged methodology rule).
- **P3 — Coverage census (the one that returns a number regardless).** Report, over all open
  events: how many have ≥ 3 quotable markets; how many the current `scan_event` actually
  evaluates; and the breakdown of skips by cause (`KXMVE` excluded / `mutually_exclusive` unset
  **and** subtitles unparseable / < 3 legs / no `ge` ladder).
  **PASS** (as a diagnostic) if the census runs. **Action trigger:** if unflagged candidate sets
  are > 10% of multi-outcome events, extend `kalshi_arb.py` to treat a `Person`-style candidate
  set as MECE-with-an-unlisted-residual (bounded, not assumed exhaustive).
- **Decision rule (stated now, so results cannot be re-scoped):** build **nothing** on P3 alone —
  it is a monitor-completeness fix, not an edge. Promote to a live-scanning book **only** if P1
  or P2 finds a lock that clears the fillability audit **and** repeats on a second scan ≥ 24h
  later (a one-shot hit is a stale-quote artifact, per the Peru/Netflix precedent). If P1 and P2
  both KILL, **the locked-arb family is closed permanently** — record it and stop re-opening it;
  the only surviving output is the P3 census plus whatever `kalshi_arb.py` coverage fix it
  triggers.

## Probe plan

- **Script:** built as `scripts/kalshi_xlock.py`, read-only, stdlib-only, structured like
  `kalshi_arb.py` (browser UA for Cloudflare 1010; `xvenue_leadlag._get/_num`), allowlisted in
  `ALLOWED_SCRIPTS` in `scripts/ops_runner.py`. Reuses `kalshi_arb.fee`, `_parse_bucket` (post-fix,
  incl. spelled-out magnitude units), `_close_ts`, and `scan_event` itself for P3 (never
  re-derives its verdict by hand, to avoid drifting from what the deployed scanner actually
  does). **`_tiles_exhaustively` turned out NOT to apply to P1/P2** on implementation — it
  guards a *partition sum* (Σ(yes) over a MECE ladder), but P1/P2 are bilateral comparisons
  between two independently-listed threshold markets, not partitions, so there is no sum to
  protect. The equivalent protection for P1/P2 is the **fillability audit** below (live
  displayed depth + same-scan-pass freshness) plus the ordinary per-leg quote-validity filter
  (`0 < ask ≤ 1`) — a bad/missing quote on either single leg is caught there, not by tiling.
- **Dataset + provenance:** Kalshi **public** market-data API only (`/events?with_nested_markets`,
  `/markets`, `/series`) — live top-of-book, single provenance, no mixing with the
  `backfill_weather_*` or live `weather_*` tables. Nothing persisted to Postgres on the first
  pass; this is a live-board scan, not a historical study.
- **Matching (precision over recall — the logged rule):** for P1, Kalshi's public API has no
  explicit "components" field, so a combo market's components are recovered from its own
  settlement **rules text** (`rules_primary`/`rules_secondary`, one `/markets/{ticker}` detail
  fetch per combo candidate, capped by `--max-combo-detail`) — the closest thing to a real
  identifier the public API exposes for this. The rules text is split on `AND`/`&`, and each
  resulting phrase is matched by **exact case-insensitive string equality** against another open
  market's own subtitle/title from a *different* series. Zero or ambiguous (≥ 2) matches on any
  phrase drops the **whole combo**, not a partial guess. For P2, legs are matched on **real
  identifiers**: series naming (`MAX`/`MIN` = touch, reused from `xvenue_crypto.py`'s own
  classification), ticker-derived direction/strike (`kalshi_arb._parse_bucket` on the subtitle),
  and close time. Any ambiguous or unmatched pair is **dropped, not guessed** (false pairs
  manufacture fake arbs — the exact failure mode of the July run and of `xvenue_crypto` v1).
- **No-lookahead:** not applicable in the usual sense (this is a point-in-time board scan), but
  both legs must be read from the **same** scan pass — the whole board fetch is timed, and if it
  spans more than 60s every hit is flagged as possibly-stale rather than trusted outright, since
  a stale leg is how a phantom lock appears.
- **Fillability audit (mandatory before reporting any hit):** displayed size ≥ 10 contracts on
  both legs; both markets `status=open`; close times within a horizon that makes the containment
  actually hold; and the leg detail printed in full so a human can confirm the logical relation
  by eye. The July scan's three "hits" all died at exactly this step — the audit is the thesis's
  main defense against repeating that.
- **Measurement:** per bound family — number of matched pairs, the distribution of
  `subset_bid − superset_ask` net of both-leg fees (not just the max), the count clearing zero,
  and the count surviving the audit. Report the **distribution**, so a null result still says
  *how far* from a lock the board sits (the 0.97–0.99 tightness read is what made the July null
  credible).
- **Promotion result:** a repeating, fillable, audited lock in P1 or P2 → hand to
  `kalshi-strategy` Phase 2 as a live scanning + execution book. Anything less → verdict logged,
  family closed.

## Cost + capacity

- **Fee math:** `ceil(7·p·(1−p))` cents per contract per leg. Two legs. Worst near 50¢ (~2¢/leg,
  4¢ round trip); cheap in the tails (~1¢/leg at 10¢ or 90¢). A lock must clear ~2–4¢ total,
  which is why the gap must be measured **net**, never gross.
- **Both legs are taken, not rested** — there is no adverse-selection haircut, because a locked
  arb crosses both spreads immediately by construction. The corresponding risk is **leg risk**:
  filling one side and not the other converts a lock into a naked position. Any executing book
  must size to the *smaller* displayed side and treat a partial fill as an immediate unwind.
- **Capacity:** unknown and probably poor — this is the main non-price reason to expect a null.
  Parlay books are thin; the P1 census must report displayed depth, not just the quote. A 3¢
  lock on 10 contracts is $0.30, i.e. noise against $100/month. **Capacity is a promotion gate,
  not an afterthought:** a lock that cannot be repeated for ≥ $20/occurrence at a
  ≥ weekly rate does not become a book.

## Correlation

- **Vs current book:** **zero shared return driver.** The one live +EV strategy (`mmsell10`) is
  maker-sell longshot premium capture; XLOCK is a two-legged taker structure whose payoff is
  bounded below by construction and does not depend on any outcome distribution. It is the most
  uncorrelated thing the portfolio could add.
- **Value to $100/mo:** if P1/P2 hit, extremely high — risk-free dollars are the only kind that
  don't need a Sharpe argument, and PORT says the binding constraint is *edge supply*. If they
  miss (the likely case), the value is the P3 census: it makes the standing arb monitor's
  denominator known, which is worth having before the next person asks this question.
