"""Shadow quote construction — three conservative policies, one price pair each.

Price convention in this module is the NATIVE one: `yes_bid` is a YES price in cents, `no_bid`
is a NO price in cents. `yes_bid + no_bid` is the combined acquisition cost of one matched pair,
which settles to 100 cents. The execution-telemetry `LocalBook` keeps both sides on the YES
scale; `book_view` converts once, at the boundary, and nothing downstream mixes scales.

All three policies produce prices at which we would GENUINELY accept a fill (the guardrail in
the handoff). None of them prices "to be seen": the reward-efficient policy may accept a
declared, bounded paired loss in exchange for reward weighting, and that loss is a first-class
output (`pair_edge_cents`), never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_BREAK_EVEN = "A_break_even"
POLICY_REWARD_EFFICIENT = "B_reward_efficient"
POLICY_CONSERVATIVE = "C_conservative"
POLICIES = (POLICY_BREAK_EVEN, POLICY_REWARD_EFFICIENT, POLICY_CONSERVATIVE)

#: Per-program capital tiers simulated in parallel (dollars). The handoff's five sizes.
CAPITAL_TIERS_USD = (25, 50, 100, 250, 500)

MIN_PRICE = 1
MAX_PRICE = 99


@dataclass(frozen=True)
class BookView:
    """The market as the quote policies see it (native prices, cents)."""

    best_yes_bid: int | None       # highest resting YES bid (yes cents)
    best_no_bid: int | None        # highest resting NO bid (no cents)
    yes_depth_at_best: float = 0.0
    no_depth_at_best: float = 0.0
    # Recent activity, for the conservative policy's distance rule.
    trades_last_5m: int = 0
    price_range_5m_cents: int = 0   # max - min traded yes price over the lookback

    @property
    def spread_cents(self) -> int | None:
        """YES spread: yes ask (= 100 - best NO bid) minus yes bid."""
        if self.best_yes_bid is None or self.best_no_bid is None:
            return None
        return (100 - self.best_no_bid) - self.best_yes_bid


def book_view_from_local(book, *, trades_last_5m: int = 0, price_range_5m_cents: int = 0) -> BookView:
    """Convert an execution-telemetry `LocalBook` (both sides on the YES scale) once."""
    yb = book.best_yes_bid()
    ya = book.best_yes_ask()            # min yes-scale price on the NO side == 100 - best NO bid
    return BookView(
        best_yes_bid=yb,
        best_no_bid=(100 - ya) if ya is not None else None,
        yes_depth_at_best=float(book.yes.get(yb, 0.0)) if yb is not None else 0.0,
        no_depth_at_best=float(book.no.get(ya, 0.0)) if ya is not None else 0.0,
        trades_last_5m=trades_last_5m,
        price_range_5m_cents=price_range_5m_cents,
    )


@dataclass(frozen=True)
class QuotePair:
    policy: str
    yes_bid: int                 # yes cents
    no_bid: int                  # no cents
    pair_cost_cents: int         # yes_bid + no_bid: what a matched pair costs before fees
    pair_edge_cents: float       # 100 - pair_cost - maker fees on both legs (negative = declared loss)
    reason: str                  # how the prices were chosen (audit)
    yes_joins_best: bool
    no_joins_best: bool

    @property
    def yes_bid_yes_scale(self) -> int:
        return self.yes_bid

    @property
    def no_bid_yes_scale(self) -> int:
        """Where the NO bid sits on the YES price scale (the LocalBook convention)."""
        return 100 - self.no_bid


def _clamp(p: int) -> int:
    return max(MIN_PRICE, min(MAX_PRICE, int(p)))


def pair_edge(yes_bid: int, no_bid: int, maker_fee_cents_per_pair: float) -> float:
    return 100.0 - (yes_bid + no_bid) - maker_fee_cents_per_pair


def _step_down_to_budget(yes_bid: int, no_bid: int, *, max_cost: int) -> tuple[int, int, str]:
    """Lower one side at a time until yes+no <= max_cost. Alternates, starting from the side
    with the larger price (the more expensive leg is the one carrying more capital)."""
    steps = 0
    while yes_bid + no_bid > max_cost and (yes_bid > MIN_PRICE or no_bid > MIN_PRICE):
        if (yes_bid >= no_bid and yes_bid > MIN_PRICE) or no_bid <= MIN_PRICE:
            yes_bid -= 1
        else:
            no_bid -= 1
        steps += 1
    return yes_bid, no_bid, f"stepped_down_{steps}"


def build_quote(policy: str, view: BookView, *, maker_fee_cents_per_pair: float = 0.0,
                max_pair_loss_cents: float = 1.0,
                conservative_ticks: int | None = None) -> QuotePair | None:
    """One price pair for `policy`, or None when the book gives nothing to quote against.

    A: join both best bids if the pair breaks even after fees, otherwise step down until it does.
    B: join both best bids as long as the paired loss is within `max_pair_loss_cents`; beyond
       that fall back to A. The loss is declared in `pair_edge_cents`.
    C: rest `conservative_ticks` behind each best bid, where the tick count rises with recent
       trade intensity and price range; then enforce break-even like A.
    """
    if view.best_yes_bid is None or view.best_no_bid is None:
        return None
    yb, nb = _clamp(view.best_yes_bid), _clamp(view.best_no_bid)
    fee = float(maker_fee_cents_per_pair)
    # The largest pair cost that still breaks even after fees (integer cents; fee rounds against us).
    break_even_cost = int(100 - fee) if fee == int(fee) else int(100 - fee)  # floor
    if policy == POLICY_BREAK_EVEN:
        y, n, how = _step_down_to_budget(yb, nb, max_cost=break_even_cost)
        reason = "join_best" if how == "stepped_down_0" else how
    elif policy == POLICY_REWARD_EFFICIENT:
        if yb + nb <= break_even_cost + int(max_pair_loss_cents):
            y, n, reason = yb, nb, "join_best_within_loss_budget"
        else:
            y, n, how = _step_down_to_budget(yb, nb, max_cost=break_even_cost)
            reason = f"loss_budget_exceeded_{how}"
    elif policy == POLICY_CONSERVATIVE:
        ticks = conservative_ticks if conservative_ticks is not None else conservative_distance(view)
        y, n = _clamp(yb - ticks), _clamp(nb - ticks)
        y, n, how = _step_down_to_budget(y, n, max_cost=break_even_cost)
        reason = f"behind_{ticks}_ticks" + ("" if how == "stepped_down_0" else f"_{how}")
    else:
        raise ValueError(f"unknown policy {policy!r}")
    if y < MIN_PRICE or n < MIN_PRICE:
        return None
    return QuotePair(
        policy=policy, yes_bid=y, no_bid=n, pair_cost_cents=y + n,
        pair_edge_cents=round(pair_edge(y, n, fee), 4), reason=reason,
        yes_joins_best=(y == yb), no_joins_best=(n == nb),
    )


def conservative_distance(view: BookView) -> int:
    """Ticks behind the best bid for policy C: 1 in a quiet market, more when the tape is
    active or the price has been moving. Deliberately coarse — it is a policy, not a model."""
    ticks = 1
    if view.trades_last_5m >= 10:
        ticks += 1
    if view.trades_last_5m >= 40:
        ticks += 1
    if view.price_range_5m_cents >= 3:
        ticks += 1
    if view.price_range_5m_cents >= 8:
        ticks += 1
    return ticks


def quantity_for_capital(capital_usd: float, pair_cost_cents: int, *,
                         target_size: float | None = None) -> int:
    """Contracts PER SIDE such that both sides filling costs at most `capital_usd`
    (collateral = yes_bid*qty + no_bid*qty). Capped at the program's Target Size when known:
    contracts beyond it earn no additional incentive weight, so the capital is better unused."""
    if pair_cost_cents <= 0:
        return 0
    qty = int(capital_usd * 100 // pair_cost_cents)
    if target_size is not None and target_size > 0:
        qty = min(qty, int(target_size))
    return max(0, qty)


def capital_required_usd(yes_bid: int, no_bid: int, qty: int) -> float:
    return round((yes_bid + no_bid) * qty / 100.0, 2)
