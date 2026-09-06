# mmsell universe review — do we know what we are selling?

**Built 2026-09-05.** Code: `kalshi_bot/mmsell/universe.py` (tiers + manifest),
`MmSellTracker._live_tier_blocks` (the live bar), `_book_admits_series` (the per-book filter).
Report: `scripts/mmsell_universe_review.py` (ops-runnable). Tests:
`tests/test_mmsell_universe_review.py`.

> ## This is a governance rule, not an edge filter. Do not report it as one.
>
> Over the measured window the **unclassified slice was profitable** (+$45.18 all-time across
> the family). A graduated series can be catastrophic: **`KXNFLSPREAD` is classified, has 382
> settled markets, and has lost $166.55** — more than a third of the family's 30-day paper P&L,
> in one series nobody was watching.
>
> Graduation says *we know what this contract is and we have history on it*. It never says *this
> contract makes money*. The two are independent, and conflating them is how a governance rule
> quietly becomes an unvalidated strategy. Every other idea in this repo needs statistical power
> we do not have; **this one needs none, because it makes no claim about returns.**

## The problem, measured

mmsell sells any cheap tail it finds, and Kalshi lists new series faster than anyone classifies
them. Across the whole mmsell family (all time, 400 traded series):

| | |
|---|---|
| series in **no** taxonomy | **81 of 400** |
| settled markets in them | **1,158** |
| of which the **new season** | **786 (68%)** |

The unclassified flow is not a long tail of oddities. It is overwhelmingly the Sept–Nov regime
change arriving before the taxonomy did:

| series | settled markets |
|---|---|
| `KXNCAAFSPREAD` | 204 |
| `KXNCAAFTOTAL` | 184 |
| `KXEPLTOTAL` | 80 |
| `KXEPLSCORE` | 66 |
| `KXNCAAFGAME` | 42 |
| `KXEPLSPREAD`, Serie A, Bundesliga, Ligue 1, `KXNFL1HSPREAD` | 210 |

And the share is **rising**, because it tracks new listings rather than being a fixed background
rate. Share of 30-day trades in unclassified series, by book age:

| book | unclassified share | note |
|---|---|---|
| `Dmmsell10` | **20.2%** | the newest LIVE canary |
| `Cmmsell10` | 18.0% | LIVE |
| `Lmmsell10` | 9.1% | LIVE, older |
| `mmsell10` | 6.3% | paper, oldest |
| `mmsell10a`, `mmsell1`, `Tmmsell4` | 0.6–1.1% | long-lived paper books |

Old books show the historical average; the newest live book shows the **current** rate. One in
five real-money trades was in a contract nobody had ever reviewed.

## The tiers

| tier | meaning | may trade |
|---|---|---|
| `GRADUATED` | classified **and** reviewed **and** carrying own settled history | anywhere, live included |
| `IN_REVIEW` | classified, but too thin for anyone to have reviewed it | **paper only** |
| `UNCLASSIFIED` | in no taxonomy at all | nothing that opts into tiering |

`UNCLASSIFIED` **beats the manifest**: a series with no market-type entry can never read as
graduated even if its prefix is in `GRADUATED_SERIES` by mistake, because knowing its history is
not the same as knowing how it settles. Both tables have to agree before anything trades it live.

**Seeded** from every series the family has traded with ≥20 settled markets of own history *and*
a market-type classification: **138 series, 87.5% of all settled markets.** Everything below that
starts at `IN_REVIEW`.

## Why a static manifest and not a live row count

Graduation is a **reviewed act** — someone looked at the series and said we understand how it
settles. Deriving it from a row count at entry time would make it automatic, which is exactly
what it must not be: a series would silently graduate itself by trading enough, and the review
the tier exists to force would never happen. Same argument `SERIES_TYPES` makes for being a
hand-audited table rather than a regex — the classification IS the claim, so it has to be
reviewable in a diff.

## What is gated, and what deliberately is not

**Live is gated** (`mmsell_live_min_tier`, default `graduated`). It gates the live **mirror**
only, at the point the entry would become a real order. It can only ever *refuse* an entry,
never add one, so it moves real-money exposure in the safe direction only. Set it to
`unclassified` to restore the pre-2026-09-05 behaviour exactly.

**A second, separate live bar exists: `mmsell_live_skip_series`** (added 2026-09-06,
XOS-000022, `docs/MMSELL_NFLSPREAD_LOSS_CELL.md`). It pauses REAL MONEY on named series —
`KXNFLSPREAD` today — and it is **not** a tier. A series can be fully `GRADUATED` and still
be paused; that is exactly the KXNFLSPREAD case, and it is why the tier bar above did not
stop the loss this document opens with. Keep the two apart when reading the counters:
`skipped_live_tier` is *we have never reviewed this contract*, `skipped_live_paused` is
*we have reviewed it and it is currently bleeding*. Both refuse live only; both leave paper
alone.

**Paper is NOT gated, and that is load-bearing.** Paper is how an unreviewed series accumulates
the history that graduates it. Barring paper too would make the quarantine permanent by
construction: a series could never earn its way out, and the tier would be a one-way ratchet
that slowly starves the book. Paper keeps collecting; only real money waits.

A per-book `universe=` filter exists for experiment books that want to select on tier — it is
`None` on every existing book, so tiering is inert for the running cohort.

## The review loop

1. Run the report: `{"type": "script", "name": "mmsell_universe_review"}`. The `UNCLASSIFIED`
   table is a work queue ordered by supply — the top rows cost the most coverage.
2. Gather settlement evidence with `scripts/mmsell_taxonomy_audit.py`, which proposes a settle
   mode per prefix from Kalshi's own rules documents and returns `INSUFFICIENT_EVIDENCE` rather
   than guessing.
3. Classifying a series edits `SERIES_TYPES`, which is **shared platform semantics** — it goes
   through Platform Change Review, not through whichever session noticed the gap.
4. Graduating a series is a PR adding its prefix to `GRADUATED_SERIES`.

Neither step happens automatically. That is the point.

## Known limitations

- **The manifest's threshold (≥20 settled markets) is a judgement, not a result.** It was chosen
  to cover 87.5% of flow while leaving a real `IN_REVIEW` tier. Nothing measures whether 20 is
  the right number, and nothing here claims it is.
- **Scoped to the mmsell family.** `SERIES_TYPES` is mmsell's taxonomy; `theta`, `weather` and
  `pin15` trade series it was never meant to cover, so applying these tiers to them would
  manufacture false gaps. The report defaults to mmsell books for that reason (`--all-strategies`
  overrides, with the caveat printed).
- **This removes ~12.5% of live flow at the current manifest.** That is the exposure being given
  up, and it is given up on a knowledge argument rather than a P&L one.
