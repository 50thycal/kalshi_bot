"""Acceptance evidence for `kalshi_bot/experiment_os/weather_bands.py`.

Proved against the real service under NEW_ONLY enforcement:
  * it arms nothing — the only tagged deployment is PAPER;
  * each spec parses as the band and families it claims, disjoint from the shipped cell;
  * each arm has its own winter-spanning kill gate, and every metric has a provider;
  * the books trade what they claim through the real tracker, and never anything else.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import db
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import weather_bands as pkg
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.experiment_os.metrics import REGISTRY
from kalshi_bot.mmsell.tracker import MmSellTracker


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _new_only(session):
    enf.record_enforcement_change(
        session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )


@pytest.fixture
def registered(xos_session, xos_platform):
    _new_only(xos_session)
    out = pkg.register(xos_session, actor="research-lab")
    xos_session.commit()
    return out


def _version(session):
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    return exp, read.latest_version(session, exp)


def test_registration_lands_at_paper_with_three_arms_one_control(registered, xos_session):
    exp, ver = _version(xos_session)
    assert exp.state == LifecycleState.PAPER.value and ver.frozen_at is not None
    arms = {a.arm_key: a.role for a in read.arms_for(xos_session, ver)}
    assert arms == {pkg.UPPER_ARM: ArmRole.CONTROL.value,
                    pkg.HIGHT_ULTRA_ARM: ArmRole.TREATMENT.value,
                    pkg.RAIN_ULTRA_ARM: ArmRole.TREATMENT.value}


def test_nothing_registered_here_can_reach_real_money(registered, xos_session):
    _exp, ver = _version(xos_session)
    for epoch in read.epochs_for(xos_session, ver):
        for dep in read.deployments_for(xos_session, epoch):
            assert dep.kind != DeploymentKind.LIVE.value
            assert dep.stage != LifecycleState.LIVE_CANARY.value
    assert all(t is None for _a, t in read.deployment_arms(xos_session, registered["probe"]))
    tags = {a.arm_key: t for a, t in read.deployment_arms(xos_session, registered["paper"])}
    assert tags == {pkg.UPPER_ARM: pkg.UPPER_TAG, pkg.HIGHT_ULTRA_ARM: pkg.HIGHT_ULTRA_TAG,
                    pkg.RAIN_ULTRA_ARM: pkg.RAIN_ULTRA_TAG}


def test_tags_are_prefix_safe_against_each_other_and_every_configured_tag(settings):
    mine = list(pkg.BOOK_SPECS)
    others = [b["tag"] for b in settings.mmsell_variant_list] + [
        "Tempmmsell1", "Rainmmsell1", "Rainmmsell2"]
    for tag in mine:
        assert "mmsell" in tag and len(tag) <= 24
        for other in mine + others:
            if other != tag:
                assert not tag.startswith(other) and not other.startswith(tag), (tag, other)


def test_specs_parse_as_the_claimed_bands_disjoint_from_the_cell(settings):
    settings.mmsell_variants = ";".join(f"{t}:{b}" for t, b in pkg.BOOK_SPECS.items())
    books = {v["tag"]: v for v in settings.mmsell_variant_list}
    assert set(books) == set(pkg.BOOK_SPECS)
    up, hu, ru = books[pkg.UPPER_TAG], books[pkg.HIGHT_ULTRA_TAG], books[pkg.RAIN_ULTRA_TAG]
    assert (up["lo"], up["hi"], up["maxyes"]) == (10.0, 16.0, 15.0)
    assert (hu["lo"], hu["hi"], hu["maxyes"]) == (1.0, 5.0, 4.0)
    assert (ru["lo"], ru["hi"], ru["maxyes"]) == (1.0, 5.0, 4.0)
    # Disjoint from the shipped cell (mid 5-10, YES <= 7): ultra's mid <= ask <= 4 < 5, and the
    # upper band needs mid >= 10 while the cell's mid <= ask <= 7.
    assert hu["maxyes"] < 5 and ru["maxyes"] < 5 and up["lo"] > 7
    assert up["optin"] == ["KXHIGH"] and up["only"] == ["KXHIGH"]
    assert hu["optin"] == ["KXHIGH"] and hu["only"] == ["KXHIGHT"]
    assert ru["onlyx"] == ["KXRAIN"] and ru["optin"] == []


def test_each_arm_has_its_own_winter_spanning_kill_gate(registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert set(gates) == set(pkg.GATE_KEYS.values())
    for arm, key in pkg.GATE_KEYS.items():
        spec = gates[key].spec_json
        assert gates[key].to_state != LifecycleState.LIVE_CANARY.value
        assert spec["sample"] == {arm: {"metric": "settled_days", "op": ">=",
                                        "value": pkg.SAMPLE_FLOOR_DAYS}}
        assert all(c["arm"] == arm for c in spec["pass_all"] + spec["fail_any"])
        named = {c["metric"] for c in spec["pass_all"] + spec["fail_any"]}
        named |= {c["min_evidence"]["metric"] for c in spec["fail_any"] if "min_evidence" in c}
        assert all(m in REGISTRY and REGISTRY[m].provided for m in named)
    earliest = datetime(2026, 10, 15, tzinfo=timezone.utc) + timedelta(days=pkg.SAMPLE_FLOOR_DAYS)
    assert earliest >= datetime(2027, 2, 10, tzinfo=timezone.utc)


def test_floor_can_be_raised_never_lowered_and_registration_is_once(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="research-lab")


def test_floor_is_never_lowered(xos_session, xos_platform):
    _new_only(xos_session)
    with pytest.raises(svc.ExperimentOsError, match="stricter, never weaker"):
        pkg.register(xos_session, actor="research-lab",
                     promotion_sample_floor=pkg.SAMPLE_FLOOR_DAYS - 1)


def test_package_is_registered_with_its_tags_and_arms_nothing():
    from kalshi_bot.experiment_os import experiment_commands as ec

    p = ec._packages()["mmsell-weather-bands"]
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert set(p.strategy_tags) == set(pkg.BOOK_SPECS)
    assert not p.activation_vars and p.arm is None


# ------------------------------------------------------------------ through the real tracker


def _mkt(ticker, yes_bid_c, yes_ask_c, hours=20):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)
    return {"ticker": ticker, "yes_sub_title": ticker, "close_time": close.isoformat(),
            "volume_fp": "500.0", "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
            "yes_ask_dollars": f"{yes_ask_c / 100:.4f}"}


def _ob(yes_bid_c, yes_ask_c):
    return {"orderbook_fp": {"yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
                             "no_dollars": [[f"{(100 - yes_ask_c) / 100:.4f}", "300"]]}}


class _Client:
    def __init__(self, quotes):
        # quotes: {series: (yes_bid, yes_ask)} — one event and one market per series
        self._events, self._books = [], {}
        for series, (yb, ya) in quotes.items():
            ev, tk = f"{series}-26OCT16", f"{series}-26OCT16-B70"
            self._events.append({"event_ticker": ev, "series_ticker": series,
                                 "mutually_exclusive": True, "markets": [_mkt(tk, yb, ya)]})
            self._books[tk] = _ob(yb, ya)

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _run(settings, quotes):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = ";".join(f"{t}:{b}" for t, b in pkg.BOOK_SPECS.items())
    db.init_engine(settings.database_url)
    db.create_all()
    with db.session_scope() as session:
        return MmSellTracker(_Client(quotes), settings).run_once(session).per_book


def test_upper_band_trades_daily_highs_only(settings):
    books = _run(settings, {"KXHIGHNY": (11, 13), "KXHIGHTDC": (11, 13), "KXLOWTNYC": (11, 13)})
    assert books.get(pkg.UPPER_TAG) == 2          # KXHIGH and KXHIGHT, never KXLOWT
    assert books.get(pkg.HIGHT_ULTRA_TAG) is None  # wrong band
    assert books.get("mmsell") is None             # the control never sees skipped series


def test_ultra_band_trades_kxhight_and_rain_only(settings):
    books = _run(settings, {"KXHIGHNY": (2, 3), "KXHIGHTDC": (2, 3), "KXRAIN": (2, 3)})
    assert books.get(pkg.HIGHT_ULTRA_TAG) == 1     # KXHIGHT only, not KXHIGH
    assert books.get(pkg.RAIN_ULTRA_TAG) == 1
    assert books.get(pkg.UPPER_TAG) is None
