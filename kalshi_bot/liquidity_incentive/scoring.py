"""Kalshi Liquidity Incentive Program scoring — reconstructed from the published rules.

SOURCES (read 2026-09-16; the sandbox cannot reach kalshi.com, so the help-center text was
taken from search-engine excerpts and the CFTC filing titles, and the API field semantics
from a vendored copy of Kalshi's OpenAPI 3.30.0 spec — see docs/LIQUIDITY_INCENTIVE_RESEARCH.md):

  * help.kalshi.com/en/articles/13823851-liquidity-incentive-program
  * CFTC filings rules09082530054 (Aug 2025) and rules02112639183 (Feb 2026 amendment,
    effective 2026-02-28, "Snapshot Liquidity Provider Score")
  * GET /trade-api/v2/incentive_programs — `target_size_fp` (contracts), `discount_factor_bps`
    (basis points), `period_reward` ("Total reward for the period in centi-cents")

THE PUBLISHED RULES, as this module implements them (each is a `RULE_*` constant so a test
can point at the sentence it checks):

  R1  Snapshots are taken once per second.
  R2  A snapshot is EXCLUDED unless BOTH sides hold at least Target Size resting. Excluded
      snapshots pay nobody, and the period reward is prorated by qualifying/total snapshots:
      "$100 reward, 10,000 snapshots, 8,000 qualifying; a 20% score share earns
       20% x $100 x (8,000 / 10,000) = $16.00".
  R3  Reference Price (per side): walk down from the best bid to the first price level at
      which cumulative resting size reaches ONE FIFTH of Target Size.
  R4  Raw score of a resting order = size x distance multiplier. At or better than the
      Reference Price the multiplier is 1.0; k ticks below it is DiscountFactor ** k
      (DiscountFactor = discount_factor_bps / 10,000; 1.00 means no penalty).
  R5  Per side, each order's raw score is divided by the total raw score of ALL qualifying
      orders on that side. A participant's snapshot score = yes share + no share (max 2.0).
  R6  A participant's period share = their summed snapshot score over all participants'
      summed snapshot score, i.e. sum(yes share + no share) / (2 x qualifying snapshots).

ASSUMPTIONS this module makes that the excerpts do not settle (labelled `A*`, listed in the
research doc as open questions for the operator to verify against the live rewards page):

  A1  "Target Size resting on a side" means the total resting contracts on that side,
      at any price. (If Kalshi restricts it to a price band, our qualifying test is loose.)
  A2  The scoring "period" is the program's [start_date, end_date] interval, so the reward
      accrues uniformly per second across it. (quantfirm's scan made the same reading.)
  A3  Every resting order is a "qualifying order" regardless of price. (If a 3c-97c band or
      a distance cap applies, our field-score denominator is too large -> our share estimate
      is CONSERVATIVE, not optimistic.)
  A4  Our own hypothetical size is added to the field when computing our share (we would be
      in the book), and it also counts toward the Target Size test.

Everything here is DERIVED and says so: `est_*` in the tables, `ScoreEstimate` in code.
"""

from __future__ import annotations

from dataclasses import dataclass

SCORING_VERSION = "lip-v1-2026-09-16"

RULE_SNAPSHOT_SECONDS = 1.0
RULE_REFERENCE_FRACTION = 0.2          # one fifth of Target Size
RULE_MAX_SNAPSHOT_SCORE = 2.0          # yes share + no share


def discount_factor(discount_factor_bps: int | None) -> float:
    """`discount_factor_bps` -> multiplier per tick below the Reference Price. A missing
    value is read as NO penalty (1.0) — the loosest reading, which OVERstates the field's
    deep-order score and therefore UNDERstates our share; conservative for us."""
    if discount_factor_bps is None:
        return 1.0
    return max(0.0, min(1.0, int(discount_factor_bps) / 10_000.0))


def period_reward_usd(period_reward_centi_cents: int | None) -> float | None:
    """`period_reward` is in centi-cents (OpenAPI 3.30.0). 1,000,000 -> $100.00."""
    if period_reward_centi_cents is None:
        return None
    return int(period_reward_centi_cents) / 10_000.0


@dataclass(frozen=True)
class SideScore:
    """One side of one snapshot, in that side's OWN price scale (yes cents on the yes side,
    no cents on the no side) so "walk down from the best bid" is the same code for both."""

    resting_total: float               # observed: contracts resting on this side
    meets_target: bool                 # observed vs Target Size (A1)
    reference_price: int | None        # derived (R3); None when too thin to set one
    field_score: float                 # derived (R4): sum of size x multiplier over the book


def side_score(levels: dict[int, float], target_size: float | None, discount: float) -> SideScore:
    """`levels`: price (this side's cents) -> resting contracts."""
    total = float(sum(levels.values()))
    meets = (target_size is not None and target_size > 0 and total >= float(target_size))
    if not levels or target_size is None or target_size <= 0:
        return SideScore(resting_total=total, meets_target=False, reference_price=None,
                         field_score=0.0)
    ordered = sorted(levels.items(), key=lambda kv: kv[0], reverse=True)
    needed = float(target_size) * RULE_REFERENCE_FRACTION
    cum, ref = 0.0, None
    for price, size in ordered:
        cum += size
        if cum >= needed - 1e-9:
            ref = price
            break
    if ref is None:
        return SideScore(resting_total=total, meets_target=meets, reference_price=None,
                         field_score=0.0)
    score = 0.0
    for price, size in ordered:
        ticks = ref - price
        score += size * (discount ** ticks) if ticks > 0 else size
    return SideScore(resting_total=total, meets_target=meets, reference_price=ref,
                     field_score=score)


def order_multiplier(price: int, reference_price: int | None, discount: float) -> float:
    """R4 for one order at `price` (its side's scale)."""
    if reference_price is None:
        return 0.0
    ticks = reference_price - price
    return (discount ** ticks) if ticks > 0 else 1.0


@dataclass(frozen=True)
class ScoreEstimate:
    """Our hypothetical two-sided quote's snapshot economics — every number DERIVED."""

    yes: SideScore
    no: SideScore
    our_yes_multiplier: float
    our_no_multiplier: float
    our_yes_raw: float
    our_no_raw: float
    our_yes_share: float         # R5, with our size added to the field (A4)
    our_no_share: float
    snapshot_qualifies: bool     # R2 with our size included (A4)
    reward_per_second_usd: float | None   # R6 x the program's per-second accrual (A2)

    @property
    def snapshot_score(self) -> float:
        return self.our_yes_share + self.our_no_share

    @property
    def period_share(self) -> float:
        """Our share of one qualifying snapshot's payout: (yes share + no share) / 2."""
        return self.snapshot_score / RULE_MAX_SNAPSHOT_SCORE

    @property
    def reward_per_hour_usd(self) -> float | None:
        return None if self.reward_per_second_usd is None else self.reward_per_second_usd * 3600.0


def estimate(*, yes_levels: dict[int, float], no_levels: dict[int, float],
             target_size: float | None, discount_factor_bps: int | None,
             our_yes_price: int | None, our_yes_size: float,
             our_no_price: int | None, our_no_size: float,
             period_reward_usd_value: float | None, period_seconds: float | None) -> ScoreEstimate:
    """Score one snapshot with our hypothetical quote added to the book.

    Prices are NATIVE per side (yes cents for the yes book, no cents for the no book).
    `period_seconds` is the program duration (A2); reward accrues at reward / period_seconds
    per second while the snapshot qualifies, and we take `period_share` of that."""
    disc = discount_factor(discount_factor_bps)
    yes_book = dict(yes_levels)
    no_book = dict(no_levels)
    if our_yes_price is not None and our_yes_size > 0:
        yes_book[our_yes_price] = yes_book.get(our_yes_price, 0.0) + our_yes_size
    if our_no_price is not None and our_no_size > 0:
        no_book[our_no_price] = no_book.get(our_no_price, 0.0) + our_no_size
    ys = side_score(yes_book, target_size, disc)
    ns = side_score(no_book, target_size, disc)
    ym = order_multiplier(our_yes_price, ys.reference_price, disc) if our_yes_price is not None else 0.0
    nm = order_multiplier(our_no_price, ns.reference_price, disc) if our_no_price is not None else 0.0
    y_raw = our_yes_size * ym
    n_raw = our_no_size * nm
    y_share = (y_raw / ys.field_score) if ys.field_score > 0 else 0.0
    n_share = (n_raw / ns.field_score) if ns.field_score > 0 else 0.0
    qualifies = ys.meets_target and ns.meets_target
    per_sec: float | None = None
    if period_reward_usd_value is not None and period_seconds and period_seconds > 0:
        accrual = period_reward_usd_value / period_seconds
        per_sec = accrual * ((y_share + n_share) / RULE_MAX_SNAPSHOT_SCORE) if qualifies else 0.0
    return ScoreEstimate(
        yes=ys, no=ns, our_yes_multiplier=ym, our_no_multiplier=nm,
        our_yes_raw=y_raw, our_no_raw=n_raw, our_yes_share=y_share, our_no_share=n_share,
        snapshot_qualifies=qualifies, reward_per_second_usd=per_sec,
    )


def worked_example_reward(share_of_period_score: float, period_reward: float,
                          qualifying_snapshots: int, total_snapshots: int) -> float:
    """R2's published example, verbatim: 20% x $100 x (8,000 / 10,000) = $16.00."""
    if total_snapshots <= 0:
        return 0.0
    return share_of_period_score * period_reward * (qualifying_snapshots / total_snapshots)
