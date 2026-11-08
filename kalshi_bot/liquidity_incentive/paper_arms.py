"""Incentive paper strategies S1–S4 (DEC-043, DEC-044): one-sided resting bids, PAPER ONLY.

Spec: `docs/handoffs/HANDOFF-limm-paper-strategies.md`. Pre-registration (the numbers below are
frozen there before recording starts): `docs/LIMM_PAPER_STRATEGIES.md`.

This module DECIDES; the shadow collector (`collector.ShadowState`) records. Nothing here does
I/O, so every rule is a pure function a test can pin:

  * `Candidate` — one tracked market as the arms see it at a requote: both sides' best bid
    (native cents) and what a $10 bid there would earn (the ranker's measure, computed with the
    collector's own `scoring.estimate`).
  * `ArmSelector.select` — the hourly, sticky choice of markets per STRATEGY. Paired arms
    (S2, S3, S4) share one selection, so they quote the same markets in the same hours.
  * `ArmSelector.desired` — per ARM and market: the bid it would rest now (side, price, size,
    contracts), or a `Pull` with the reason it is off.

Seven arms:

  S1          weekly money-finder: highest pool per day per market, at the best bid.
  S2-best     the ranker's top quotable markets, at the best bid.
  S2-behind   same markets, one tick behind the best bid (§9.72; a 1c bid stays at 1c).
  S3-aside    state employment ladders, AAA gas, one-event ladders; at the best bid, pulled
              around verified releases, the gas window and late certainty dumps.
  S3-control  same markets, never pulled (only the common 48 h pre-close step-aside).
  S4-burst    the $1-minimum harvest band; $50 until the accrued calibrated reward clears
              $1 + margin, then off (or $10, if the programme-age study says so).
  S4-flat     same markets, $10 the whole programme.

Nothing here can place an order: it returns numbers. The collector holds a GET-only client and
writes only `incentive_*` tables.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

ARM_S1 = "S1"
ARM_S2_BEST = "S2-best"
ARM_S2_BEHIND = "S2-behind"
ARM_S3_ASIDE = "S3-aside"
ARM_S3_CONTROL = "S3-control"
ARM_S4_BURST = "S4-burst"
ARM_S4_FLAT = "S4-flat"
ARMS = (ARM_S1, ARM_S2_BEST, ARM_S2_BEHIND, ARM_S3_ASIDE, ARM_S3_CONTROL, ARM_S4_BURST,
        ARM_S4_FLAT)

S1, S2, S3, S4 = "S1", "S2", "S3", "S4"
STRATEGIES = (S1, S2, S3, S4)
ARMS_BY_STRATEGY = {
    S1: (ARM_S1,),
    S2: (ARM_S2_BEST, ARM_S2_BEHIND),
    S3: (ARM_S3_ASIDE, ARM_S3_CONTROL),
    S4: (ARM_S4_BURST, ARM_S4_FLAT),
}
STRATEGY_OF = {arm: s for s, arms in ARMS_BY_STRATEGY.items() for arm in arms}

# Pull / deselect reasons (end_reason on incentive_shadow_quotes; <= 48 chars).
PULL_BLS = "pull_bls_release"
PULL_GAS = "pull_gas_window"
PULL_ONE_EVENT_DUMP = "pull_one_event_dump"
PULL_ONE_EVENT_LATE = "pull_one_event_last_days"
PULL_BURST_DONE = "pull_burst_threshold"
PULL_BURST_WINDOW = "pull_burst_window_over"
PULL_NO_PRICE = "pull_side_not_quotable"
END_DESELECTED = "arm_deselected"
END_RESIZED = "arm_resized"

#: S3's universe (handoff §3a). Series suffix / prefix rules, frozen with the pre-registration.
S3_BLS_SUFFIX = "EMP"                       # state employment ladders (BLS State Employment)
S3_GAS_PREFIX = "KXAAAGAS"                  # AAA gas
S3_ONE_EVENT_SERIES = ("KXASPENMTNOPEN", "KXBIGSKYOPEN")   # ski opening-date ladders

#: Verified BLS State Employment releases (10:00 ET), from the ranker's KNOWN_RELEASES
#: (checked against https://www.bls.gov/schedule/2026/ on 2026-11-08). Only releases inside
#: the recording window matter; the read is ~2026-11-24.
BLS_RELEASES_UTC = (datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),)


@dataclass(frozen=True)
class ArmRules:
    """Every number the arms use. The pre-registration freezes these values; a change after
    recording starts is a new version of the experiment, never an edit."""

    cheap_max_cents: int = 10
    step_aside_hours: float = 48.0
    max_per_event: int = 3
    max_markets: tuple[tuple[str, int], ...] = ((S1, 20), (S2, 10), (S3, 10), (S4, 10))
    base_size_usd: int = 10
    s1_big_pool_day_usd: float = 50.0
    s1_big_size_usd: int = 20
    s4_burst_size_usd: int = 50
    #: contracts cap per bid, by arm ($ size / 1c, so the cap binds only at 1c-ish prices)
    max_qty: tuple[tuple[str, int], ...] = (
        (ARM_S1, 2000), (ARM_S2_BEST, 1000), (ARM_S2_BEHIND, 1000), (ARM_S3_ASIDE, 1000),
        (ARM_S3_CONTROL, 1000), (ARM_S4_BURST, 5000), (ARM_S4_FLAT, 1000))
    #: paid ÷ estimated, fixed in advance (global median, scripts/limm_estimate_calibration.py)
    calibration: float = 1.34
    #: study 3's thin rule: an hour where we would be more than this share of a side earns nothing
    thin_share: float = 0.5
    #: S4 universe: projected calibrated reward of a $10 bid to the step-aside, in this band
    s4_band_low_usd: float = 0.30
    s4_band_high_usd: float = 1.00
    #: S4-burst stops bursting once accrued calibrated reward >= $1 + this margin
    s4_margin_usd: float = 0.25
    #: None = burst whenever S4 enters; a number = burst only this many hours after the
    #: programme starts. Set by the programme-age study (docs/LIMM_PROGRAMME_AGE.md §9,
    #: rl-age-run1-1108: NOT-EARLY -> None).
    s4_burst_window_hours: float | None = None
    #: after the burst: "off" (pull) or "flat" ($10 for the rest of the programme). Late hours
    #: still pay ($0.31 per $10 bid-day >= $0.15, same run) -> "flat".
    s4_after_burst: str = "flat"
    bls_pre_hours: float = 2.0
    bls_post_hours: float = 48.0
    gas_pull_hours_utc: tuple[int, ...] = (12, 13, 14, 15)          # 12-16Z daily
    one_event_last_days: float = 3.0
    #: Watch pins. The collector keeps ~150 markets by daily pay, which drops S3's ladders and
    #: every thin programme S4 needs; while the arms are on it ALSO keeps these (beyond the cap):
    #: every selected market, S3's universe by daily pay, and S4's thin-pool funnel, newest first.
    s3_pins: int = 40
    s4_pins: int = 30
    s4_pin_pool_low_usd: float = 2.0
    s4_pin_pool_high_usd: float = 50.0
    s4_pin_min_pay_hours: float = 24.0

    def cap(self, strategy: str) -> int:
        return dict(self.max_markets).get(strategy, 0)

    def qty_cap(self, arm: str) -> int:
        return dict(self.max_qty).get(arm, 1000)


DEFAULT_RULES = ArmRules()


@dataclass(frozen=True)
class SideView:
    """One side of a candidate: its best bid (native cents) and a $10 bid's economics there."""

    price: int | None
    rate10_per_hour_usd: float = 0.0     # uncalibrated estimate, 0 when not qualifying
    qualifies10: bool = False            # both sides at Target Size with our $10 in (A4)
    share10: float = 0.0                 # our share of this side's field with $10 in


@dataclass(frozen=True)
class Candidate:
    ticker: str
    event: str
    series: str
    at: datetime
    start: datetime | None
    end: datetime | None
    close_time: datetime | None
    pool_day_usd: float | None
    yes: SideView
    no: SideView

    def side(self, name: str) -> SideView:
        return self.yes if name == "yes" else self.no

    def step_aside_at(self, rules: ArmRules) -> datetime | None:
        """When every arm leaves: the programme's end, or `step_aside_hours` before close."""
        cuts = [t for t in (self.end, (self.close_time - timedelta(hours=rules.step_aside_hours))
                            if self.close_time else None) if t is not None]
        return min(cuts) if cuts else None

    def hours_to_step_aside(self, rules: ArmRules, now: datetime) -> float:
        cut = self.step_aside_at(rules)
        return math.inf if cut is None else (cut - now).total_seconds() / 3600.0

    def best_side(self, rules: ArmRules) -> str | None:
        """The side the ranker would pick: quotable (1..cheap_max) and qualifying, highest $10
        reward; ties go to the cheaper side."""
        best, key = None, None
        for name in ("yes", "no"):
            sv = self.side(name)
            if sv.price is None or not (1 <= sv.price <= rules.cheap_max_cents) or not sv.qualifies10:
                continue
            k = (sv.rate10_per_hour_usd, -sv.price)
            if key is None or k > key:
                best, key = name, k
        return best


@dataclass
class Selection:
    ticker: str
    event: str
    side: str
    selected_at: datetime
    programme_start: datetime | None
    max_price_seen: int = 0              # for the one-event dump rule
    dumped: bool = False                 # one-event ladder: our side fell to 1c after being higher


@dataclass(frozen=True)
class ArmQuote:
    arm: str
    side: str
    price: int
    size_usd: int
    qty: int


@dataclass(frozen=True)
class Pull:
    reason: str


def series_of(ticker: str, series: str | None = None) -> str:
    return (series or ticker.split("-", 1)[0]).upper()


def event_of(ticker: str, event: str | None = None) -> str:
    if event:
        return event
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def s3_kind(series: str) -> str | None:
    s = series.upper()
    if s.endswith(S3_BLS_SUFFIX):
        return "bls"
    if s.startswith(S3_GAS_PREFIX):
        return "gas"
    if s in S3_ONE_EVENT_SERIES:
        return "one_event"
    return None


def contracts(size_usd: int, price: int, cap: int) -> int:
    if price <= 0:
        return 0
    return max(0, min(cap, int(size_usd * 100 // price)))


def projected_reward10_usd(c: Candidate, side: str, rules: ArmRules, now: datetime) -> float:
    """S4's band test: a $10 bid's calibrated reward from now to the step-aside, at the current
    rate (the ranker's measure x hours left x the fixed calibration)."""
    hours = c.hours_to_step_aside(rules, now)
    if not math.isfinite(hours) or hours <= 0:
        return 0.0
    return c.side(side).rate10_per_hour_usd * hours * rules.calibration


def bls_pulled(now: datetime, rules: ArmRules, releases=BLS_RELEASES_UTC) -> bool:
    return any(r - timedelta(hours=rules.bls_pre_hours) <= now < r + timedelta(hours=rules.bls_post_hours)
               for r in releases)


def gas_pulled(now: datetime, rules: ArmRules) -> bool:
    return now.astimezone(timezone.utc).hour in rules.gas_pull_hours_utc


@dataclass
class BurstState:
    """S4-burst's progress in one programme (market x programme start)."""

    accrued_calibrated_usd: float = 0.0
    done: bool = False


@dataclass
class ArmSelector:
    rules: ArmRules = field(default_factory=ArmRules)
    selected: dict[str, dict[str, Selection]] = field(
        default_factory=lambda: {s: {} for s in STRATEGIES})
    bursts: dict[tuple[str, str], BurstState] = field(default_factory=dict)

    # -- eligibility ------------------------------------------------------------------------
    def _active(self, c: Candidate, now: datetime) -> bool:
        if c.start is not None and c.start > now:
            return False
        if c.end is not None and c.end <= now:
            return False
        return c.hours_to_step_aside(self.rules, now) > 0

    def _in_universe(self, strategy: str, c: Candidate, side: str, now: datetime) -> bool:
        if strategy == S3:
            return s3_kind(c.series) is not None
        if strategy == S4:
            if self.rules.s4_burst_window_hours is not None and c.start is not None:
                # EARLY-PAYS: a burst only makes sense inside the window, so only programmes
                # still inside it can enter.
                if (now - c.start).total_seconds() / 3600.0 > self.rules.s4_burst_window_hours:
                    return False
            p = projected_reward10_usd(c, side, self.rules, now)
            return self.rules.s4_band_low_usd <= p < self.rules.s4_band_high_usd
        return True

    def _rank_key(self, strategy: str, c: Candidate, side: str):
        if strategy == S1:
            return (c.pool_day_usd or 0.0, c.side(side).rate10_per_hour_usd)
        if strategy == S4:
            # Every band programme is an equal harvest candidate: prefer the most programme left.
            return (c.start.timestamp() if c.start else 0.0, c.side(side).rate10_per_hour_usd)
        return (c.side(side).rate10_per_hour_usd, c.pool_day_usd or 0.0)

    def keep(self, sel: Selection, c: Candidate | None, now: datetime) -> bool:
        """A selected market stays while its programme is live, before the step-aside, and its
        selected side is still quotable on the cheap side. Qualification may flicker; it moves
        the reward (logged), not the selection."""
        if c is None or not self._active(c, now):
            return False
        p = c.side(sel.side).price
        return p is not None and 1 <= p <= self.rules.cheap_max_cents

    # -- selection --------------------------------------------------------------------------
    def select(self, candidates: dict[str, Candidate], now: datetime) -> dict[str, list[str]]:
        """Re-run every strategy's sticky selection. Returns {strategy: tickers dropped}."""
        dropped: dict[str, list[str]] = {}
        for strategy in STRATEGIES:
            chosen = self.selected[strategy]
            gone = [t for t, sel in chosen.items() if not self.keep(sel, candidates.get(t), now)]
            for t in gone:
                del chosen[t]
            dropped[strategy] = gone
            per_event: dict[str, int] = {}
            for sel in chosen.values():
                per_event[sel.event] = per_event.get(sel.event, 0) + 1
            pool = []
            for t, c in candidates.items():
                if t in chosen or not self._active(c, now):
                    continue
                side = c.best_side(self.rules)
                if side is None or not self._in_universe(strategy, c, side, now):
                    continue
                pool.append((self._rank_key(strategy, c, side), t, side))
            pool.sort(key=lambda x: (x[0], x[1]), reverse=True)
            cap = self.rules.cap(strategy)
            for _key, t, side in pool:
                if len(chosen) >= cap:
                    break
                c = candidates[t]
                if per_event.get(c.event, 0) >= self.rules.max_per_event:
                    continue
                chosen[t] = Selection(ticker=t, event=c.event, side=side, selected_at=now,
                                      programme_start=c.start)
                per_event[c.event] = per_event.get(c.event, 0) + 1
        return dropped

    def selections_for(self, ticker: str) -> dict[str, Selection]:
        return {s: sel[ticker] for s, sel in self.selected.items() if ticker in sel}

    # -- S4 burst bookkeeping ---------------------------------------------------------------
    @staticmethod
    def burst_key(ticker: str, programme_start: datetime | None) -> tuple[str, str]:
        return (ticker, programme_start.isoformat() if programme_start else "")

    def burst(self, ticker: str, programme_start: datetime | None) -> BurstState:
        return self.bursts.setdefault(self.burst_key(ticker, programme_start), BurstState())

    def add_burst_reward(self, ticker: str, programme_start: datetime | None,
                         uncalibrated_usd: float) -> None:
        b = self.burst(ticker, programme_start)
        b.accrued_calibrated_usd += uncalibrated_usd * self.rules.calibration
        if b.accrued_calibrated_usd >= 1.0 + self.rules.s4_margin_usd:
            b.done = True

    # -- what each arm rests ----------------------------------------------------------------
    def observe(self, sel: Selection, c: Candidate) -> None:
        """Track the one-event dump rule: our side's bid falls to 1c after being higher."""
        p = c.side(sel.side).price
        if p is None:
            return
        if p == 1 and sel.max_price_seen > 1:
            sel.dumped = True
        sel.max_price_seen = max(sel.max_price_seen, p)

    def desired(self, arm: str, sel: Selection, c: Candidate, now: datetime,
                pool_day_usd: float | None = None) -> ArmQuote | Pull:
        r = self.rules
        best = c.side(sel.side).price
        if best is None or not (1 <= best <= r.cheap_max_cents):
            return Pull(PULL_NO_PRICE)
        price = max(1, best - 1) if arm == ARM_S2_BEHIND else best
        size = r.base_size_usd
        if arm == ARM_S1 and (pool_day_usd if pool_day_usd is not None else c.pool_day_usd or 0.0) >= r.s1_big_pool_day_usd:
            size = r.s1_big_size_usd
        if arm == ARM_S3_ASIDE:
            kind = s3_kind(c.series)
            if kind == "bls" and bls_pulled(now, r):
                return Pull(PULL_BLS)
            if kind == "gas" and gas_pulled(now, r):
                return Pull(PULL_GAS)
            if kind == "one_event":
                if sel.dumped:
                    return Pull(PULL_ONE_EVENT_DUMP)
                if c.close_time is not None and (c.close_time - now) <= timedelta(days=r.one_event_last_days):
                    return Pull(PULL_ONE_EVENT_LATE)
        if arm == ARM_S4_BURST:
            b = self.burst(sel.ticker, sel.programme_start)
            in_window = True
            if r.s4_burst_window_hours is not None and sel.programme_start is not None:
                in_window = (now - sel.programme_start) <= timedelta(hours=r.s4_burst_window_hours)
            if b.done or not in_window:
                if r.s4_after_burst != "flat":
                    return Pull(PULL_BURST_DONE if b.done else PULL_BURST_WINDOW)
            else:
                size = r.s4_burst_size_usd
        qty = contracts(size, price, r.qty_cap(arm))
        if qty <= 0:
            return Pull(PULL_NO_PRICE)
        return ArmQuote(arm=arm, side=sel.side, price=price, size_usd=size, qty=qty)


__all__ = [
    "ARMS", "ARMS_BY_STRATEGY", "STRATEGIES", "STRATEGY_OF", "ArmRules", "ArmSelector",
    "ArmQuote", "Candidate", "Pull", "Selection", "SideView", "DEFAULT_RULES",
]
