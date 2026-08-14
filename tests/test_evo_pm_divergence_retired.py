"""`pm_divergence` is retired from the DSL because it was measured and it points the WRONG WAY.

It shipped on an assumption-free premise — "do two venues disagree about the same event?" needs
no forecasting skill of ours — and the premise was never tested. The PMDIV probe tested it over
39,740 cycles / 198 settled events (`docs/PMDIV_THESIS.md`):

    band (degF)      n     pm_err  mkt_err  pm_better%
    (-inf,-3)      146       4.60     1.02        1%
    [-3,-1)      9,797       1.90     0.65        9%
    [-1,+1)     27,945       1.05     0.76       27%
    [+1,+3)      1,840       1.44     0.85       42%
    [+3,+inf)       12       3.48     1.40        8%

The gradient is the finding: the further Polymarket strays from the Kalshi price, the more
reliably POLYMARKET is the one that is wrong. A large `pm_divergence` is not a weak signal, it
is an anti-signal, so `pm_divergence >= 5` is a rule for trading toward the mistaken venue. The
identical shape showed up for our own NWS/ensemble forecast the same day (0%/4% in its outer
bands) — on Kalshi weather the market is simply the best forecaster we have access to.

Leaving a refuted metric in `METRICS` is not neutral. Agents choose conditions from that list and
cannot re-derive this result, so every heartbeat it stays is a chance for one to build a
confidently-wrong book on it. The pre-registered decision rule in the thesis said to strip it on a
P2 failure; P2 failed with a KILL.

What survives, deliberately: the raw `polymarket` source in `inspect_data`. The venue's prices
are still DATA an agent may look at and reason about — what is withdrawn is our claim that
differencing it against Kalshi's mid is a tradable edge.

`spot_vs_strike` is untouched: it resolves for every BTC/ETH ladder market, it is replayable on
the `crypto` dataset, and nothing has refuted it.
"""

from __future__ import annotations

from kalshi_bot.evo import data_access, sandbox, signals
from kalshi_bot.evo.marketdata import Quote
from kalshi_bot.evo.strategy_spec import METRICS, validate_spec


def _spec_with(metric: str) -> tuple:
    return validate_spec({
        "name": "probe_spec",
        "universe": {"series_prefixes": [], "min_volume": 0, "max_spread_cents": 99,
                     "min_hours_to_close": 0.0, "max_hours_to_close": 500},
        "entry": {"side": "yes", "style": "taker", "min_price_cents": 1,
                  "max_price_cents": 99, "size_contracts": 5,
                  "conditions": [{"metric": metric, "op": ">=", "value": 5}]},
        "exit": {"mode": "settlement"},
        "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
                 "max_cost_per_position_usd": 50},
    })


def test_the_metric_is_gone_from_the_dsl_vocabulary():
    assert "pm_divergence" not in METRICS


def test_a_spec_gating_on_it_is_rejected_with_a_usable_error():
    """Rejected, not silently ignored. An agent that writes it must be told, or it will keep
    writing it and wonder why nothing trades."""
    spec, err = _spec_with("pm_divergence")
    assert spec is None
    assert err and "pm_divergence" in err


def test_the_surviving_external_signal_still_works():
    """The retirement is targeted, not a retreat from external signals. spot_vs_strike is
    replayable on the crypto dataset and nothing has refuted it."""
    assert "spot_vs_strike" in METRICS
    assert "spot_vs_strike" in signals.SIGNAL_METRICS
    spec, err = _spec_with("spot_vs_strike")
    assert err is None and spec is not None


def test_nothing_still_computes_or_carries_it():
    """A dead field that keeps being populated is a trap for the next reader: it looks like a
    live signal, and someone re-adds the metric to reach it."""
    assert "pm_divergence" not in signals.SIGNAL_METRICS
    assert "pm_divergence" not in Quote.__dataclass_fields__
    assert not hasattr(signals, "_pm_divergence")


def test_no_dataset_advertises_it_as_replayable():
    for dataset, metrics in sandbox.DATASET_SIGNALS.items():
        assert "pm_divergence" not in metrics, dataset


def test_the_raw_polymarket_source_is_deliberately_kept(evo_session):
    """What was refuted is the DERIVED metric, not the venue's data. An agent may still read
    Polymarket prices and form its own view — that is the whole point of inspect_data."""
    assert "polymarket" in data_access.SOURCES


def test_polymarket_reads_carry_the_bucket_bounds():
    """A yes_prob with no low_f/high_f is unusable: the agent gets a probability without
    knowing WHICH temperature bucket it belongs to, so it cannot line it up against anything.
    The source shipped that way and was inert for it."""
    cols = data_access.SOURCES["polymarket"]["columns"]
    assert "low_f" in cols and "high_f" in cols
