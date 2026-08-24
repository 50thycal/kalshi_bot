# WS-003 — MMSELL non-crypto settlement-mode paper design

**Phase:** DECIDE
**Status:** Blocked
**Created:** 2026-08-24
**Updated:** 2026-08-24

## Goal

Determine whether restricting a maker-sell book to **scheduled-settlement** markets
improves its economics, holding the universe and the entry-price band constant — as a
paper-only, pre-registered comparison whose treatment is defined by the taxonomy rather
than by a series list.

## Context

Successor to an invalid live A/B whose two arms differed in universe as well as in rule, so
the measured difference could not be attributed. The correction is a disjoint partition of
one universe by settlement mode, at one entry band, on paper.

The design has been pre-registered and returned for approval three times, each time coming
back with a precondition unmet rather than an answer. It has never been registered: **no
Version, epoch, deployment or arm exists for it.**

## Current Mental Model

```text
 one eligible universe:  non-crypto, 5-7c effective entry band
                 │
     ┌───────────┴────────────┐
     ▼                        ▼
 T: mode=scheduled      C: mode=in_play+discrete      ← disjoint partition,
                                                        not subset-vs-superset
 unclassified → NEITHER arm, and counted.
 If that count exceeds 5% of eligible supply the comparison is BLOCKED_DATA.

 primary estimand:  delta.cents_per_contract = T − C
 independent unit:  the EVENT, not the settled market
                    (markets on one event share the thing being settled)
```

Two properties do the work, and both have bitten this design before:

- **The treatment is the taxonomy, never a series list.** A series-substring allowlist is
  not "scheduled settlement"; it is a list of tickers that happen to look like one.
- **The event is the unit.** A four-way total on one game is not four independent draws.
  Ignoring that makes every interval too narrow — the correction runs in one direction only.

## Decisions Made

- **Disjoint partition, one primary estimand.** The operational "should the book concentrate
  on scheduled markets?" question is a deterministic rescaling of the primary delta and gets
  no gate of its own.
- **Unclassified markets are excluded from both arms and counted**, with a 5% block
  threshold fixed before measurement so it could not be chosen after seeing which way the
  exclusions fell.
- **Minimum useful effect +2¢/contract**, chosen from prior deconfounded estimates and *not*
  chosen to be reachable within a tolerable calendar.
- **`only=` is not the treatment**, anywhere in the design.

## Open Decisions

- **D1.** Is the calendar acceptable at +2¢? The measured horizon is long, and it is
  conditional on an unmeasured correlation parameter — the plausible range spans years, not
  months. Moving the effect size after seeing supply is not an option; abandoning or
  reshaping the comparison is. Operator decision.
- **D2.** Does the crypto-exclusion defect get fixed before registration, or does the design
  accept it? The arms' exclusion is a substring blocklist and it drops at least one
  unrelated non-crypto market whose ticker happens to contain a coin abbreviation. Tiny
  today, open-ended in principle. Changing it is a change to the scientific contract, so it
  belongs to Research Lab, not to the platform review that found it.
- **D3.** What measures the event-correlation parameter, and on what data, given no arm
  exists to measure it on? Until this is answered the sample floor is a range rather than a
  number.

## Assumptions

- Paper fills are close enough to right for a *rule* question — the comparison is about what
  to trade, not about how well an order fills. If the arms were ever promoted this stops
  holding.
- The per-market dispersion figure used for sizing predates the taxonomy repair and the
  universe it describes has since changed. It must be re-measured before registration.
- The candidate stream is the binding constraint on an arm, not settled-trade composition.

## Non-Goals

- Any live-money version of this comparison.
- Reporting a subset-versus-superset delta as an independent treatment effect.
- Deciding the effect size to fit an available calendar.
- Editing `SERIES_TYPES` from this workstream — that is WS-002.

## Build Card

Not ready. The design is pre-registered but three preconditions are unmet, and a Build Card
issued now would ask the operator to approve a horizon computed from numbers known to be
provisional.

## Implementation State

None. **No Version, epoch, deployment or arm exists.** Nothing has been registered, armed,
promoted or started.

## Review State

Not started — there is nothing implemented to review.

## Related Decisions

None in this log yet. The design's own pre-registration lives in
`docs/RESEARCH_MMSELL_2X2_PAPER_DESIGN.md`; if and when it registers, the consequential
records are Experiment OS objects.

## Related PRs

- `50thycal/kalshi_bot#257` — the taxonomy repair this design is gated behind (WS-002).

## Related Experiment OS objects

Linked, not restated.

- `MARKET_TAXONOMY` — the shared semantic the treatment selects on.
- `mmsell-price-ceiling` — the incumbent whose entry band the control arm reuses.

## Next Step

Blocked on three things, in order: (1) WS-002's platform revision registered and PR #257
merged, so the eligible universe is settled; (2) the crypto-exclusion substring defect
decided (D2); (3) the event-correlation parameter measured (D3). Nothing should be
registered until all three land.
