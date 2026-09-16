"""Reward / trading economics for one shadow quote pair, and the read-only opportunity ranking.

    net = incentive reward (DERIVED) + paired trading P&L + single-leg P&L − fees − capital cost

Every component is returned separately. The opportunity score divides the sum by capital
required and is for RANKING ONLY (Phase 0 places nothing); its components are surfaced beside
it so a good score with bad trading economics is visible as exactly that.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .fees import FeeRule, maker_fee_usd
from .fills import OUTCOME_NONE, ShadowLeg, pair_outcome

# Opportunity states (the handoff's vocabulary). None of them submits anything.
STATE_IGNORE = "IGNORE"          # economics clearly poor
STATE_WATCH = "WATCH"            # attractive but too competitive / risky right now
STATE_SHADOW = "SHADOW"          # worth simulating
STATE_POC_CANDIDATE = "POC_CANDIDATE"   # strong enough that a tiny live test could be argued
STATES = (STATE_IGNORE, STATE_WATCH, STATE_SHADOW, STATE_POC_CANDIDATE)

#: Opportunity cost of capital, per day, used ONLY in the ranking (the handoff's "capital
#: opportunity cost"). 3.75%/yr is Kalshi's own cash APY, so parked collateral forgoes it.
CAPITAL_COST_PER_DAY = 0.0375 / 365.0


@dataclass(frozen=True)
class PairEconomics:
    """The accounting for one quote pair under ONE fill model."""

    fill_model: str
    outcome: str
    yes_filled: float
    no_filled: float
    matched_pairs: float
    single_leg_side: str | None
    single_leg_qty: float
    capital_required_usd: float
    capital_hours: float
    est_reward_usd: float          # DERIVED (scoring model) — accrued while resting
    est_reward_yes_usd: float
    est_reward_no_usd: float
    paired_pnl_usd: float          # matched pairs: (100 - yes_bid - no_bid) x pairs
    fees_usd: float                # maker fees on every simulated fill
    single_leg_mtm_usd: float | None    # unmatched contracts marked at the bid, if a mark exists
    single_leg_max_adverse_usd: float | None
    net_before_settlement_usd: float    # reward + paired + single-leg MTM (0 if unknown) - fees

    def as_dict(self) -> dict:
        return asdict(self)


def pair_economics(*, fill_model: str, yes_leg: ShadowLeg, no_leg: ShadowLeg,
                   yes_bid: int, no_bid: int, rest_seconds: float,
                   capital_required_usd: float, fee_rule: FeeRule,
                   reward_yes_usd: float, reward_no_usd: float,
                   single_leg_mark_bid_cents: int | None = None,
                   single_leg_worst_bid_cents: int | None = None) -> PairEconomics:
    """Compute the pair's economics under one model.

    `reward_*_usd` are the scoring model's accrued estimates for each side over the rest
    window (already prorated by the seconds each side actually rested — a filled side stops
    earning). Marks are the best bid on the unmatched side, in that side's native cents."""
    y = yes_leg.filled.get(fill_model, 0.0)
    n = no_leg.filled.get(fill_model, 0.0)
    matched = min(y, n)
    single_side: str | None = None
    single_qty = 0.0
    single_price = 0
    if y > n + 1e-9:
        single_side, single_qty, single_price = "yes", y - n, yes_bid
    elif n > y + 1e-9:
        single_side, single_qty, single_price = "no", n - y, no_bid
    paired = round((100 - yes_bid - no_bid) * matched / 100.0, 4)
    fees = round(maker_fee_usd(fee_rule, y, yes_bid) + maker_fee_usd(fee_rule, n, no_bid), 4)
    mtm: float | None = None
    worst: float | None = None
    if single_side is not None and single_leg_mark_bid_cents is not None:
        mtm = round((single_leg_mark_bid_cents - single_price) * single_qty / 100.0, 4)
    if single_side is not None and single_leg_worst_bid_cents is not None:
        worst = round((single_leg_worst_bid_cents - single_price) * single_qty / 100.0, 4)
    reward = round(reward_yes_usd + reward_no_usd, 6)
    net = round(reward + paired + (mtm or 0.0) - fees, 4)
    return PairEconomics(
        fill_model=fill_model,
        outcome=pair_outcome(yes_leg, no_leg, fill_model),
        yes_filled=y, no_filled=n, matched_pairs=matched,
        single_leg_side=single_side, single_leg_qty=single_qty,
        capital_required_usd=capital_required_usd,
        capital_hours=round(capital_required_usd * rest_seconds / 3600.0, 4),
        est_reward_usd=reward, est_reward_yes_usd=round(reward_yes_usd, 6),
        est_reward_no_usd=round(reward_no_usd, 6),
        paired_pnl_usd=paired, fees_usd=fees,
        single_leg_mtm_usd=mtm, single_leg_max_adverse_usd=worst,
        net_before_settlement_usd=net,
    )


def settlement_pnl_usd(*, single_leg_side: str | None, single_leg_qty: float,
                       single_price_cents: int, result: str | None) -> float | None:
    """Unmatched contracts at settlement: a YES contract pays 100 on `yes`, 0 on `no`."""
    if single_leg_side is None or single_leg_qty <= 0 or result not in ("yes", "no"):
        return None
    wins = (result == single_leg_side)
    value = 100 if wins else 0
    return round((value - single_price_cents) * single_leg_qty / 100.0, 4)


# ---------------------------------------------------------------- opportunity ranking


@dataclass(frozen=True)
class Opportunity:
    """A read-only ranking row for one active program under one policy / capital tier.
    Rates are per DAY. Every component is its own field; `score` is their sum over capital."""

    est_reward_per_day_usd: float
    est_paired_value_per_day_usd: float
    est_single_leg_cost_per_day_usd: float
    est_fees_per_day_usd: float
    capital_required_usd: float
    capital_cost_per_day_usd: float
    est_net_per_day_usd: float
    score: float | None                  # net per day / capital required (None when no capital)
    reward_per_capital_dollar_per_day: float | None
    state: str
    state_reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def classify(*, est_net_per_day_usd: float, est_reward_per_day_usd: float,
             capital_required_usd: float, both_sides_meet_target: bool,
             our_share_of_field: float, single_leg_cost_per_day_usd: float,
             sample_outcomes: int) -> tuple[str, str]:
    """Human-readable state + reason. Thresholds are deliberately coarse and stated here:

    IGNORE        net/day <= 0, or no capital can be deployed, or reward/day < $0.05
    WATCH         positive net but our estimated share of the field < 2% (too competitive),
                  or single-leg cost dominates (> reward), or the snapshot does not qualify
    SHADOW        positive net, share >= 2%, qualifying — worth simulating
    POC_CANDIDATE SHADOW plus net/day >= $0.25 at this tier AND >= 50 recorded outcomes
                  (evidence, not a first impression). Never submits orders in Phase 0.
    """
    if capital_required_usd <= 0:
        return STATE_IGNORE, "no capital deployable at this tier"
    if est_reward_per_day_usd < 0.05:
        return STATE_IGNORE, "estimated reward under $0.05/day"
    if est_net_per_day_usd <= 0:
        return STATE_IGNORE, "estimated net per day is not positive"
    if not both_sides_meet_target:
        return STATE_WATCH, "snapshots would not qualify: a side is under Target Size"
    if our_share_of_field < 0.02:
        return STATE_WATCH, "our estimated field share is under 2% (too competitive)"
    if single_leg_cost_per_day_usd > est_reward_per_day_usd:
        return STATE_WATCH, "single-leg adverse-selection cost exceeds the reward"
    if est_net_per_day_usd >= 0.25 and sample_outcomes >= 50:
        return STATE_POC_CANDIDATE, "positive net >= $0.25/day with >= 50 recorded outcomes"
    return STATE_SHADOW, "positive net; simulating"


def opportunity(*, est_reward_per_day_usd: float, est_paired_value_per_day_usd: float,
                est_single_leg_cost_per_day_usd: float, est_fees_per_day_usd: float,
                capital_required_usd: float, both_sides_meet_target: bool,
                our_share_of_field: float, sample_outcomes: int) -> Opportunity:
    cap_cost = round(capital_required_usd * CAPITAL_COST_PER_DAY, 6)
    net = round(est_reward_per_day_usd + est_paired_value_per_day_usd
                - est_single_leg_cost_per_day_usd - est_fees_per_day_usd - cap_cost, 6)
    score = (net / capital_required_usd) if capital_required_usd > 0 else None
    rpc = (est_reward_per_day_usd / capital_required_usd) if capital_required_usd > 0 else None
    state, reason = classify(
        est_net_per_day_usd=net, est_reward_per_day_usd=est_reward_per_day_usd,
        capital_required_usd=capital_required_usd, both_sides_meet_target=both_sides_meet_target,
        our_share_of_field=our_share_of_field,
        single_leg_cost_per_day_usd=est_single_leg_cost_per_day_usd,
        sample_outcomes=sample_outcomes)
    return Opportunity(
        est_reward_per_day_usd=round(est_reward_per_day_usd, 6),
        est_paired_value_per_day_usd=round(est_paired_value_per_day_usd, 6),
        est_single_leg_cost_per_day_usd=round(est_single_leg_cost_per_day_usd, 6),
        est_fees_per_day_usd=round(est_fees_per_day_usd, 6),
        capital_required_usd=round(capital_required_usd, 2),
        capital_cost_per_day_usd=cap_cost, est_net_per_day_usd=net,
        score=None if score is None else round(score, 6),
        reward_per_capital_dollar_per_day=None if rpc is None else round(rpc, 6),
        state=state, state_reason=reason,
    )


__all__ = [
    "OUTCOME_NONE", "PairEconomics", "Opportunity", "pair_economics", "settlement_pnl_usd",
    "opportunity", "classify", "STATES",
]
