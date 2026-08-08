"""The fee reconciliation (scripts/mmsell_fee_recon) — paper's fee model vs Kalshi's bill.

This is a MEASUREMENT, not a probe: it re-baselines every gate stated in absolute cents. So the
tests pin the two things that would silently distort it —

  * the fee formula itself, including that the ceiling applies to the whole FILL rather than
    per contract (which is why a 58-contract fill costs far less per contract than 58
    one-contract fills, and why clip size interacts with the fee at all);
  * the maker/taker book classification, since the taker-entry books are the CONTROL. If they
    were misfiled as makers there would be no control left and a plain arithmetic error would
    be indistinguishable from the maker-vs-taker gap the script exists to measure.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

_SPEC = importlib.util.spec_from_file_location("mmsell_fee_recon", SCRIPTS / "mmsell_fee_recon.py")
fr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fr)  # type: ignore[union-attr]


def test_coefficients_match_the_published_schedule():
    assert fr.TAKER_COEFF == 0.07
    assert fr.MAKER_COEFF == 0.0175
    # The maker rate is exactly a quarter of the taker rate.
    assert round(fr.MAKER_COEFF / fr.TAKER_COEFF, 6) == 0.25


def test_fee_formula_matches_the_paper_engine():
    """coeff=0.07 at qty=1 must reproduce paper/engine.py:kalshi_fee exactly — that equality is
    what makes the 'paper' column in the report the real booked number."""
    from kalshi_bot.paper.engine import kalshi_fee

    for price in (5, 6, 11, 20, 50, 80, 93, 94, 99):
        assert fr.modeled_fee(fr.TAKER_COEFF, price, 1) == kalshi_fee(price, 1)


def test_ceiling_is_per_fill_not_per_contract():
    """The whole clip-size effect lives here. 58 contracts in ONE fill are ceiled once; the same
    58 contracts as separate fills would each ceil to a full cent."""
    one_fill = fr.modeled_fee(fr.TAKER_COEFF, 47, 58)
    as_singles = 58 * fr.modeled_fee(fr.TAKER_COEFF, 47, 1)
    assert one_fill == 1.02          # ceiled once over the whole fill
    assert as_singles == 1.16        # 58 x ceil(0.0174) = 58 x 2c
    assert one_fill < as_singles
    # The effect is larger in the cheap band, where the raw per-contract fee is a small fraction
    # of a cent so the ceiling dominates: at 93c a 20-lot costs 10c against 20c as singles.
    assert fr.modeled_fee(fr.TAKER_COEFF, 93, 1) == 0.01     # raw 0.0046 -> ceils to a full cent
    assert fr.modeled_fee(fr.TAKER_COEFF, 93, 20) == 0.10    # raw 0.0911 -> one ceiling
    assert round(20 * fr.modeled_fee(fr.TAKER_COEFF, 93, 1), 2) == 0.20


def test_cheap_band_single_contract_ceils_to_a_cent_on_both_rates():
    """The reading that made an earlier analysis wrong: at 1 contract in the cheap band BOTH
    formulas round to 1c, which looks like 'no maker discount'. The measurement showed real
    maker fills cost ~0.01c — i.e. the maker fee is not merely discounted, it is not charged on
    these series. Pinned so the arithmetic claim and the empirical claim stay distinguishable."""
    for yes_price in (5, 6, 7, 11):
        no_price = 100 - yes_price
        assert fr.modeled_fee(fr.TAKER_COEFF, no_price, 1) == 0.01
        assert fr.modeled_fee(fr.MAKER_COEFF, no_price, 1) == 0.01


def test_taker_premium_only_appears_mid_book():
    """A taker pays more than a maker only when the contract price sits between ~17c and ~83c;
    outside that the ceiling equalises them at 1 contract."""
    assert fr.modeled_fee(fr.TAKER_COEFF, 50, 1) > fr.modeled_fee(fr.MAKER_COEFF, 50, 1)
    assert fr.modeled_fee(fr.TAKER_COEFF, 94, 1) == fr.modeled_fee(fr.MAKER_COEFF, 94, 1)
    assert fr.modeled_fee(fr.TAKER_COEFF, 6, 1) == fr.modeled_fee(fr.MAKER_COEFF, 6, 1)


def test_maker_entry_classification_keeps_the_control_intact():
    """The weather books enter as YES-takers and are the control. Misfiling them as makers would
    destroy the comparison that makes this conclusive."""
    for maker in ("mmsell", "mmsell3", "mmsell10", "mmsellA1", "Wmmsell1", "Tmmsell6",
                  "theta4", "mmsell10b"):
        assert fr.is_maker_entry(maker) is True, maker
    for taker in ("weather_fav_h20", "weather_low_fav_h14", "probe", "(unmatched)", "", None):
        assert fr.is_maker_entry(taker) is False, taker


def test_zero_contracts_never_divides():
    assert fr._cpc(0.0, 0) is None
    assert fr._cpc(1.0, 100) == 1.0
