# WS-002 — MMSELL settlement-taxonomy repair

**Phase:** REVIEW
**Status:** Blocked
**Created:** 2026-08-24
**Updated:** 2026-08-24

## Goal

Repair the `SERIES_TYPES` settlement-mode taxonomy far enough that a `mode=`-defined book
selects on settlement behaviour rather than on classification debt — and, specifically, far
enough for the non-crypto MMSELL paper comparison (WS-003) to have a measurable candidate
universe.

## Context

`SERIES_TYPES` had no entry for 198 series prefixes. `classify()` returns
`("unclassified", "unknown")` for those, and an unknown series is admitted by **no**
allowlist filter — so every `mode=`/`mtype=` book was silently excluding them. In the
canonical census population that was a seventh of the eligible universe, against a
pre-registered 5% unclassified bar — a bar WS-003's comparison cannot clear while the
classification debt stands.

The first scoping named six crypto series. The audit put the real scope two orders of
magnitude higher.

`SERIES_TYPES` is shared platform semantics read by live `mode=` books, so the change is a
Platform Change Review event with its own impact analysis — not a research edit.

## Current Mental Model

```text
 Kalshi series ──► classify(series)  [longest-prefix match over SERIES_TYPES]
                          │
        ┌─────────────────┼──────────────────┬───────────────────┐
        ▼                 ▼                  ▼                   ▼
   in_play           scheduled           discrete          unclassified
   live contest      published figure    occurrence in     → admitted by NO
   reveals it        at a named          a window            allowlist filter
                     instant                                 → silently excluded

 The repair is ADDITIVE: no pre-existing prefix changes type or mode, and the
 type/mode vocabularies are unchanged. What changes is WHICH MARKETS a
 mode=/mtype= book can see — which is a candidate-population change, i.e. an
 epoch boundary for the arms that read it.
```

The discriminator applied was written down **before** the case-by-case, and the batch was
frozen before any classification decision was made — so the review could not be steered by
which side of the 5% bar it would land on.

## Decisions Made

- **Freeze the whole unresolved population, not a ranked slice.** With no cutoff there is
  no cutoff to move.
- **Weak evidence decides nothing.** Expiration-to-close distance and price-path shape
  corroborate; a bare clock time is a game *start* time; `can_close_early` is set on 100% of
  the population and cannot reach `classify()` at all.
- **Refuse prefixes shorter than five characters**, even where the settlement evidence is
  unambiguous. A short prefix mapping to a treatment-eligible mode is a catch-all by another
  name.
- **Take the conservative side at every boundary** — ambiguous cases land in the control
  mode, never the treatment-eligible one.
- **Do not edit an experiment's arm spec on the researcher's behalf.** A defect found in the
  arms' crypto exclusion is routed, not fixed here.

## Open Decisions

- **D1.** Cadence for taxonomy maintenance. The repair is a snapshot: a fresh-window read
  showed a batch of series absent from the canonical window entirely, arriving with new
  competition seasons. The gate is evaluated at *every* read, so an unmaintained taxonomy
  re-blocks a long run months in. Options: periodic audit, a coverage alarm, or accept
  drift and re-run before each registration. Operator decision; nothing blocks it but nobody
  has chosen.

## Assumptions

- Kalshi's published title and rules text remain the strong signal. Its
  `settlement_source` field was empty across the entire evidence corpus, so nothing depends
  on it.
- The repair stays additive. If a future pass needs to *re-decide* a grandfathered entry,
  that is a separate review with its own impact analysis.

## Non-Goals

- Registering, arming, promoting or starting any MMSELL experiment.
- Changing the pre-registered 5% unclassified threshold.
- Using `only=` as a settlement-treatment proxy.
- Re-deciding pre-existing taxonomy entries.

## Build Card

Approved as the Platform Change Review brief of 2026-08-24. Summary: reproduce the
canonical census, freeze the review batch before classifying it, classify only on strong
published evidence, run the platform impact review, implement with tests, and re-measure —
without registering anything.

## Implementation State

PR open — see *Related PRs*. Both copies of `SERIES_TYPES` updated byte-identically; new
test file covering copy identity, per-prefix classification, prefix shadowing, the three known traps,
and the `mode=`-not-`only=` guarantee. Census, frozen manifest, per-prefix review, evidence
corpus, platform-impact analysis and post-repair measurement committed under
`docs/mmsell_taxonomy_repair/`.

## Review State

Awaiting review. **Not safe to merge yet** — see *Next Step*.

## Related Decisions

None in this log. The consequential records for this work are Experiment OS objects: the
proposed `MARKET_TAXONOMY` revision and its impact dispositions, plus `XOS-000006`.

## Related PRs

- `50thycal/kalshi_bot#257` — the repair, its evidence and its measurement.

## Related Experiment OS objects

Linked, not restated — query Experiment OS for their current state.

- `XOS-000006` — the durable ticket raised for this repair.
- `MARKET_TAXONOMY` — the shared semantic the repair changes. The repair proposes the
  successor revision `settlement_repair_2026_08_24`; which revision is active and whether
  the successor is registered are Experiment OS's to answer.
- `mmsell-type-tight` — the experiment whose arms read this taxonomy.

## Next Step

**Merge guard.** Before merging PR #257, query Experiment OS and verify that
`MARKET_TAXONOMY:settlement_repair_2026_08_24` is registered and that every required
pre-merge impact disposition is accepted. Merging ahead of that would change shared
semantics under active arms with no accounted impact record. Registration needs a writable
`DATABASE_URL`, which no agent session has — so it is an operator act, not a merge step.
