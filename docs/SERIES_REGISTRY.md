# Series registry — what we are allowed to trade, and how well we know it

**Built 2026-09-06.** Code: `kalshi_bot/registry/` (states + manifest + arrival observer),
`kalshi_bot/registry/series_manifest.json` (the decision ledger), `models.SeriesObservation`
(the observation ledger), `kalshi_bot/mmsell/universe.py` (mmsell's import surface),
`MmSellTracker._live_tier_blocks` (the live bar), `_book_admits_series` (the per-book filter).
Report: `scripts/series_registry_review.py` (ops-runnable). Tests:
`tests/test_series_registry.py`, `tests/test_mmsell_universe_review.py`.

Supersedes the static manifest described in `docs/MMSELL_UNIVERSE_REVIEW.md`, which remains the
record of the measurement that motivated the bar.

> ## This is a governance rule, not an edge filter. Do not report it as one.
>
> The unclassified slice has been **profitable** (+$45.18 all-time across the mmsell family). A
> graduated series can be catastrophic: **`KXNFLSPREAD` is classified, has 382 settled markets,
> and has lost $166.55**. Graduation says *we know what this contract is and we have history on
> it*. It never says *this contract makes money*.

## The problem

Kalshi lists new series faster than anyone reviews them, and every book that selects candidates
by contract structure rather than by an explicit allowlist picks a new one up the cycle it
appears. Measured 2026-09-05: **20.2% of the live canary `Dmmsell10`'s trades over 30 days were
in series no taxonomy covered**, 68% of that being a new season arriving. The share *rises* over
time, because it tracks listings — older books read 0.7–6%, the newest live book read 20%.

PR #338 put a bar in front of the live mirror. It did not answer the question underneath: **how
does a market we have never seen become one we are willing to trade, and who decides?**

## Two ledgers, one boundary

| | holds | moves by | canonical for |
|---|---|---|---|
| `registry/series_manifest.json` | state, reviewer, review date, reason | **a PR** | decisions |
| `series_observations` (Postgres) | first seen, last seen, breadth | **the worker** | facts |

A decision should be reviewable, and in this project a PR is how decisions get reviewed — so
the state of a series lives in git and moves only by diff. Arrival is a fact nobody should have
to commit, so it lives in the database. `scripts/series_registry_review.py` joins them.

The manifest is **JSON, not a Python table**, and that is deliberate. Ops-channel scripts are
self-contained (stdlib + psycopg — the runner never installs this package), which historically
forced any table both sides need to be *copied*, with a test asserting the copies match
(`market_types.SERIES_TYPES` / `scripts/mmsell_market_types.py` still work this way). A JSON
file read from disk by both has no second copy to drift, which matters far more for a ledger
that changes weekly than for a taxonomy that rarely changes. PR #338's duplicated
`GRADUATED_SERIES` in `scripts/mmsell_universe_review.py` is gone for this reason.

## The states

| state | meaning | who may trade it |
|---|---|---|
| `identified` | seen, and nothing more — no taxonomy entry, or no manifest row | paper, and books naming no minimum |
| `in_review` | classified by the market-type taxonomy, not yet admitted by a reviewer | as above |
| `graduated` | admitted | anywhere, **live included** |
| `barred` | someone looked at it and refused it | **nobody** |

`identified`, `in_review`, `graduated` are a ladder: a book naming a minimum admits that rung
and everything above it. **`barred` is not on the ladder** — it is a veto that fails every
minimum *including the `None` that admits everything else*, because a refusal that only binds
books which opted in is decorative.

`unclassified` — PR #338's name for the bottom rung — still parses as `identified` everywhere a
state is read from config, an env var or a book spec, so no deployed `MMSELL_VARIANTS` string or
`mmsell_live_min_tier` value changed meaning. `tests/test_series_registry.py` proves series-by-
series, at every minimum a book can name, that **nothing changed side** when the frozenset
became a manifest — the registry gates a live canary's universe, and a silent widening there
puts real money into unreviewed contracts.

A **taxonomy gap outranks a manifest row**: a series `SERIES_TYPES` does not know reads as
`identified` even if a row graduates its prefix, because we would still not know how it settles.
`barred` is the one exception that outranks the gap in turn — an explicit refusal must not be
rescued by a hole in the taxonomy. Rows match by **longest prefix**, so `KXNFL` graduated with
`KXNFLSPREAD` barred is expressible.

## The graduation bar is two claims

|  | proves | recorded as |
|---|---|---|
| **mechanism understood** | someone read Kalshi's settlement rules for the series | `rules_reviewed_at`, `rules_reviewed_by` |
| **history sufficient** | enough of our own settled markets that it settles as we think | derived from `paper_trades` |

**Neither implies the other**, and PR #338's seed proved only the second: it admitted every
series with ≥20 own settled markets and a classification, then inferred the first. `KXNFLSPREAD`
cleared exactly that bar.

So all **138 rows were grandfathered on 2026-09-06 carrying `rules_reviewed_at: null`**. They
trade live today — revoking that wholesale would empty the live universe overnight and stop the
canary collecting — and they are simultaneously the **audit backlog**, ranked by live exposure
so the series actually risking money are read first. The row records the debt honestly instead
of hiding it behind a bar that was never met.

## Arrival detection

The scan reports every series it sees **before any category, volume or liquidity filter**: the
registry's question is what Kalshi has offered us, not what some book was willing to take, and
an arrivals queue filtered by the very scope decisions a review should revisit is not
trustworthy. Observations accumulate in memory and are written once per cycle — one `SELECT`
plus a handful of upserts — and `SeriesObserver.flush` swallows its own errors, because a lost
cycle of arrival data costs a delayed review while an exception would cost trading.

`first_seen_at` is the column that cannot be reconstructed later, and it is **not backfilled**.
`markets.created_at` records when the collector first wrote a market row, which for series
predating it is when the backfill ran, not when Kalshi listed the series; seeding from it would
manufacture arrival dates that look authoritative and are wrong. The column earns its meaning
going forward, which is the only way it can be true.

`markets_seen` is **breadth**, not a running total: the most distinct markets of the series ever
seen offered at once. A cumulative count would drift upward forever as dated markets roll.

## Running the review

```json
{"type": "script", "name": "series_registry_review"}
{"type": "script", "name": "series_registry_review", "args": ["--section", "backlog"]}
{"type": "script", "name": "series_registry_review", "args": ["--min-settled", "50"]}
```

Three sections, each a queue:

- **ARRIVALS** — observed, governed by no manifest row. Every row is a market that became
  available and that nothing has reviewed.
- **BACKLOG** — graduated with no recorded rules review, **live cells first**, then by |P&L|. A
  live cell that has barely traded outranks a large paper-only one: the review protects real
  money, and |P&L| sizes what a misunderstanding would already have cost.
- **CANDIDATES** — not graduated, carrying enough own settled history to be worth a reviewer's
  time. **History is half the bar**; volume alone graduates nothing.

The report **authorizes nothing**. Ranking exists so a human reads the right rows first.

## Scope today, and what is next

The registry is a **platform-level ledger** — `kalshi_bot/registry/`, not under `mmsell/` —
because all eight strategy families ask the same question, and the report already reads settled
history across all of them. What is wired so far:

- **arrival detection**: all families (it is fed from the raw listing sweep, which is shared).
- **the report**: all families.
- **the entry gate**: **mmsell only**, via `mmsell_live_min_tier` and the per-book `universe=`
  key. The other seven have their own entry paths and are unchanged; opting each in is
  follow-up work, not something to do silently underneath a running book.

Next: **the audit** — retire the 138-row `rules_reviewed_at: null` backlog, live cells first,
using `scripts/mmsell_taxonomy_audit.py` to gather the settlement evidence. `KXNFLSPREAD` is
row one and already has an open investigation behind it.
