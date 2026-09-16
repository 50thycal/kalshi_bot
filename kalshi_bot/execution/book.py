"""A local reconstruction of one market's order book from the `orderbook_delta` stream, and
the derived features a queue sample carries.

Price convention: the collector subscribes with `use_yes_price: true`, so BOTH sides arrive on
the YES price scale. A NO bid at no-price 7c is therefore side `no` at price 93. Our mmsell
order — sell YES at 93 == buy NO at 7 — is a NO bid and lives on side `no` at price 93. A
"better" NO bid is a higher NO price, i.e. a LOWER yes price.

The book is BIDS ONLY (Kalshi has no separate asks; YES + NO = $1). best_yes_bid is the max
price on side `yes`; best_yes_ask is the min price on side `no` (the best NO bid, seen from the
YES side). Everything here is derived and recomputable from `execution_book_events`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .parse import levels

SIDES = ("yes", "no")
PRICE_CONVENTION = "yes"


@dataclass
class LocalBook:
    market_ticker: str
    yes: dict[int, float] = field(default_factory=dict)   # yes-leg price -> contracts
    no: dict[int, float] = field(default_factory=dict)    # yes-leg price -> contracts
    valid: bool = False           # False until a snapshot has been applied
    sid: int | None = None
    last_seq: int | None = None
    snapshot_at: datetime | None = None
    last_update_at: datetime | None = None

    def apply_snapshot(self, msg: dict, *, sid: int | None, seq: int | None,
                       received_at: datetime) -> None:
        self.yes = dict(levels(msg.get("yes_dollars_fp")))
        self.no = dict(levels(msg.get("no_dollars_fp")))
        self.valid = True
        self.sid = sid
        self.last_seq = seq
        self.snapshot_at = received_at
        self.last_update_at = received_at

    def apply_delta(self, side: str, price_cents: int, delta: float,
                    received_at: datetime) -> float | None:
        """Apply one delta; returns the level quantity after, or None when the delta would
        make a level negative (a scale mismatch or a missed snapshot — the caller records it
        and treats the book as invalid rather than storing a fiction)."""
        table = self.yes if side == "yes" else self.no
        after = table.get(price_cents, 0.0) + delta
        if after < -1e-9:
            self.valid = False
            return None
        if after <= 1e-9:
            table.pop(price_cents, None)
        else:
            table[price_cents] = after
        self.last_update_at = received_at
        return max(after, 0.0)

    # -- derived -----------------------------------------------------------------------------
    def best_yes_bid(self) -> int | None:
        return max(self.yes) if self.yes else None

    def best_yes_ask(self) -> int | None:
        return min(self.no) if self.no else None

    def depth(self, side: str) -> float:
        return float(sum((self.yes if side == "yes" else self.no).values()))

    def top_depth(self, side: str, n: int = 3) -> float:
        table = self.yes if side == "yes" else self.no
        ordered = sorted(table.items(), key=lambda kv: kv[0], reverse=(side == "yes"))
        return float(sum(q for _, q in ordered[:n]))

    def features_for(self, side: str, price_cents: int) -> dict:
        """Book features around OUR level. `side` and `price_cents` are our order's level in
        the yes-price convention (see module docstring)."""
        table = self.yes if side == "yes" else self.no
        if side == "no":
            better = {p: q for p, q in table.items() if p < price_cents}
            worse = {p: q for p, q in table.items() if p > price_cents}
        else:
            better = {p: q for p, q in table.items() if p > price_cents}
            worse = {p: q for p, q in table.items() if p < price_cents}
        bid, ask = self.best_yes_bid(), self.best_yes_ask()
        yes_depth, no_depth = self.depth("yes"), self.depth("no")
        total = yes_depth + no_depth
        return {
            "price_convention": PRICE_CONVENTION,
            "book_valid": self.valid,
            "book_age_s": None,
            "best_yes_bid": bid,
            "best_yes_ask": ask,
            "spread": (ask - bid) if bid is not None and ask is not None else None,
            "mid": ((ask + bid) / 2.0) if bid is not None and ask is not None else None,
            "our_side": side,
            "our_price": price_cents,
            "distance_from_best": (
                (price_cents - ask) if side == "no" and ask is not None else
                (bid - price_cents) if side == "yes" and bid is not None else None),
            "qty_at_our_price": float(table.get(price_cents, 0.0)),
            "qty_better": float(sum(better.values())),
            "levels_better": len(better),
            "qty_worse": float(sum(worse.values())),
            "yes_depth": yes_depth,
            "no_depth": no_depth,
            "yes_top3": self.top_depth("yes"),
            "no_top3": self.top_depth("no"),
            "imbalance": ((yes_depth - no_depth) / total) if total > 0 else None,
        }
