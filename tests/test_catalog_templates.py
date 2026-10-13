"""Series rule templates: one owner approval covers every market with the same wording."""

import json
import threading
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from kalshi_bot.catalog import templates
from kalshi_bot.catalog.research import cohort
from kalshi_bot.catalog.service import (
    APPROVALS,
    apply_approvals,
    make_server,
    recorded_approvals,
)
from kalshi_bot.catalog.store import RULE_FIELDS, SEMANTIC_FIELDS, Store, now

MARKETS = json.loads(
    (Path(__file__).parent / "fixtures/catalog_template_markets.json").read_text()
)["markets"]
APPROVED = {item["ticker"]: item for item in json.loads(APPROVALS.read_text())["items"]}


def raw(ticker, **changes):
    market = {k: v for k, v in deepcopy(MARKETS[ticker]).items() if not k.startswith("_")}
    return {**market, **changes}


def put(store, ticker, **changes):
    return store.upsert("market", ticker, raw(ticker, **changes), ticker.split("-")[0])


def approval(series, exemplars=None, **changes):
    item = deepcopy(APPROVED[series])
    if exemplars is not None:
        item["exemplars"] = exemplars
    return {**item, **changes}


def live(id, ticker):
    return {"id": id, "market_ticker": ticker, "strategy": "mmsell10", "side": "no",
            "quantity": 1, "status": "settled", "created_at": "2026-10-01T00:00:00+00:00"}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.db")


def test_every_rule_field_has_one_template_role():
    roles = (templates.TEXT_FIELDS, templates.EXACT_FIELDS, templates.STRIKE_FIELDS,
             templates.KEYED_FIELDS, templates.DISPLAY_FIELDS, templates.CLOCK_FIELDS)
    assigned = [field for role in roles for field in role]
    assert sorted(assigned) == sorted(RULE_FIELDS)


def test_strikes_dates_and_names_share_a_template_but_wording_does_not():
    btc = "KXBTCD-26OCT1617-T87999.99", "KXBTCD-26JUL1317-T62999.99"
    assert raw(btc[0])["rules_primary"] != raw(btc[1])["rules_primary"]
    assert templates.template_hash(raw(btc[0])) == templates.template_hash(raw(btc[1]))
    # The strikeout participation test was reworded during trading: never one approval.
    ks = [t for t in MARKETS if t.startswith("KXMLBKS-")]
    assert len({templates.template_hash(raw(t)) for t in ks}) == 2
    base = raw(btc[0])
    for changed in (
        {"rules_primary": base["rules_primary"].replace("resolves to Yes", "resolves to No")},
        {"rules_primary": base["rules_primary"].replace("above", "below")},
        {"settlement_timer_seconds": 120},
        {"strike_type": "less"},
        {"cap_strike": 90000},
        {"rules_secondary": base["rules_secondary"].replace("60 RTI", "30 RTI")},
    ):
        assert templates.template_hash({**base, **changed}) != templates.template_hash(base)
    # A number that is not the market's own strike stays literal.
    assert "60 <name> prices" in templates.template_document(base)["text"]["rules_secondary"]


def test_series_approval_covers_siblings_and_leaves_reworded_markets_unreviewed(store):
    put(store, "KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-11")
    early = [t for t in MARKETS if t.startswith("KXMLBKS-") and "26OCT07" not in t][0]
    put(store, early)
    for ticker in ("KXBTCD-26OCT1617-T87999.99", "KXBTCD-26JUL1317-T62999.99"):
        put(store, ticker)
    sibling = raw("KXBTCD-26OCT1617-T87999.99")
    sibling = {
        **sibling,
        "event_ticker": "KXBTCD-26NOV0217",
        "floor_strike": 101249.99,
        "rules_primary": sibling["rules_primary"].replace("87999.99", "101249.99").replace(
            "Oct 16", "Nov 2"),
        "yes_sub_title": "$101,250 or above",
        "no_sub_title": "$101,250 or above",
        "title": "Bitcoin price on Nov 2, 2026?",
        "close_time": "2026-11-02T22:00:00Z",
    }
    store.upsert("market", "KXBTCD-26NOV0217-T101249.99", sibling, "KXBTCD")
    assert store.get("market", "KXBTCD-26NOV0217-T101249.99")["review_status"] == "needs_review"

    result = store.review(approval("KXBTCD"))
    assert len(result["templates"]) == 1  # both exemplars share one wording version
    document = store.get("market", "KXBTCD-26NOV0217-T101249.99")
    assert document["review_status"] == "reviewed"
    assert document["review_basis"] == "series_template"
    assert document["semantics"]["shared_exposure_key"] == "candidate-index-time:BRTI:26NOV0217"
    assert document["semantics"]["observation_start"] is None
    assert document["review"]["approved_by"] == "Calvin (owner)"
    # Another series' approval never covers it, and the reworded KS market is untouched.
    assert store.get("market", early)["review_status"] == "needs_review"
    # Re-posting is a no-op, and the approved series keeps detailed history.
    again = store.review(approval("KXBTCD"))
    assert all(t["already_recorded"] for t in again["templates"])
    assert len(store.series_template_reviews("KXBTCD")) == 1
    with store.connect() as db:
        reasons = {row[0] for row in db.execute(
            "SELECT reason FROM selection_members WHERE scope='series' AND ticker='KXBTCD'")}
    assert "series_review" in reasons


def test_rewording_after_approval_returns_market_to_review(store):
    put(store, "KXBTCD-26OCT1617-T87999.99")
    put(store, "KXBTCD-26JUL1317-T62999.99")
    store.review(approval("KXBTCD"))
    ticker = "KXBTCD-26OCT1617-T87999.99"
    reworded = raw(ticker)
    reworded["rules_secondary"] += " A delayed index publication settles at the next value."
    store.upsert("market", ticker, reworded, "KXBTCD")
    assert store.get("market", ticker)["review_status"] == "needs_review"


def test_approval_refuses_text_the_owner_did_not_read(store):
    with pytest.raises(LookupError, match="not a KXBTCD market"):
        store.review(approval("KXBTCD"))
    put(store, "KXBTCD-26OCT1617-T87999.99", rules_secondary="Changed after review.")
    with pytest.raises(LookupError, match="differ from the text"):
        store.review(approval("KXBTCD", exemplars=approval("KXBTCD")["exemplars"][:1]))
    put(store, "KXWTI-26OCT1314-T95.49")
    wrong = [{"market": "KXWTI-26OCT1314-T95.49",
              "rules_hash": approval("KXWTI")["exemplars"][0]["rules_hash"]}]
    with pytest.raises(LookupError, match="not a KXBTCD market"):
        store.review(approval("KXBTCD", exemplars=wrong))
    assert store.series_template_reviews("KXBTCD") == []


def test_template_semantics_must_be_market_independent(store):
    put(store, "KXRAIN-26OCT13-NYC")
    exemplar = approval("KXRAIN")["exemplars"][:1]
    timed = approval("KXRAIN", exemplars=exemplar)
    timed["semantics"] = {**timed["semantics"], "observation_start": now()}
    with pytest.raises(ValueError, match="observation"):
        store.review(timed)
    bad_key = approval("KXRAIN", exemplars=exemplar)
    bad_key["semantics"] = {**bad_key["semantics"], "shared_exposure_key": "event:{market}"}
    with pytest.raises(ValueError, match="event_ticker"):
        store.review(bad_key)
    unknown = approval("KXRAIN", exemplars=exemplar)
    unknown["semantics"] = {**unknown["semantics"], "settlement_source": None}
    with pytest.raises(ValueError, match="must be known"):
        store.review(unknown)
    with pytest.raises(ValueError, match="approver"):
        store.review({k: v for k, v in approval("KXRAIN").items() if k != "approved_by"})


def test_every_recorded_approval_binds_to_the_text_that_was_read(store):
    for item in APPROVED.values():
        assert set(item["semantics"]) >= set(SEMANTIC_FIELDS)
        assert item["scope"]["qualified"] is False and item["scope"]["confidence_score"] is None
        for exemplar in item["exemplars"]:
            put(store, exemplar["market"])
        result = store.review(item)
        assert result["templates"]
        for exemplar in item["exemplars"]:
            document = store.get("market", exemplar["market"])
            assert document["review_status"] == "reviewed", exemplar["market"]
            assert "{" not in document["semantics"]["shared_exposure_key"]


def test_coverage_counts_traded_markets_and_names_unapproved_wording(store):
    covered = ("KXBTCD-26OCT1617-T87999.99", "KXBTCD-26JUL1317-T62999.99")
    for ticker in covered:
        put(store, ticker)
    other = raw(covered[0])
    other["rules_secondary"] = "An older wording version."
    store.upsert("market", "KXBTCD-26AUG0117-T63749.99", other, "KXBTCD")
    store.evidence_page(
        "live",
        [live(i, t) for i, t in enumerate(
            (*covered, "KXBTCD-26AUG0117-T63749.99", "KXBTCD-26SEP0117-T1"), start=1)],
        {"after": 0},
    )
    before = store.template_coverage("KXBTCD")
    assert (before["traded_markets"], before["reviewed"]) == (4, 0)
    store.review(approval("KXBTCD"))
    after = store.template_coverage("KXBTCD")
    assert after["reviewed"] == 2
    assert after["reviewed_by_basis"] == {"series_template": 2}
    assert after["listing_missing"] == 1
    assert after["unreviewed_templates"][0]["samples"] == ["KXBTCD-26AUG0117-T63749.99"]


def test_research_cohort_reads_a_series_approval_only_after_it_was_given(store):
    put(store, "KXBTCD-26OCT1617-T87999.99")
    put(store, "KXBTCD-26JUL1317-T62999.99")
    store.evidence_page("live", [live(1, "KXBTCD-26JUL1317-T62999.99")], {"after": 0})
    before = cohort(store, series="KXBTCD", as_of=now())
    store.review(approval("KXBTCD", approved_at=now()))
    item = {i["market_ticker"]: i for i in cohort(store, series="KXBTCD")["items"]}[
        "KXBTCD-26JUL1317-T62999.99"]
    assert item["reviews_at_as_of"]["market"]["basis"] == "series_template"
    assert "market_review_missing_at_as_of" not in item["missing"]
    early = {i["market_ticker"]: i for i in before["items"]}["KXBTCD-26JUL1317-T62999.99"]
    assert "market_review_missing_at_as_of" in early["missing"]


def test_recorded_approvals_post_only_through_the_authenticated_api(store):
    for exemplar in APPROVED["KXRAIN"]["exemplars"]:
        put(store, exemplar["market"])
    assert {i["approval"]["ticker"] for i in recorded_approvals(store)} == set(APPROVED)
    assert not any(i["posted"] for i in recorded_approvals(store))
    server = make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            assert client.get("/v1/approvals").status_code == 401
            assert client.post("/v1/reviews", json=APPROVED["KXRAIN"]).status_code == 401
            client.headers["Authorization"] = "Bearer " + "a" * 32
            assert client.post("/v1/reviews", json=APPROVED["KXRAIN"]).status_code == 201
            items = {i["approval"]["ticker"]: i for i in client.get("/v1/approvals").json()["items"]}
            assert items["KXRAIN"]["posted"] and not items["KXBTCD"]["posted"]
            assert client.post("/v1/reviews", json=APPROVED["KXBTCD"]).status_code == 409
            coverage = client.get("/v1/template-coverage?series=KXRAIN").json()["items"][0]
            assert coverage["series"] == "KXRAIN"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_merged_approvals_apply_at_start_and_refusals_never_block(store, caplog):
    for exemplar in APPROVED["KXBTCD"]["exemplars"]:
        put(store, exemplar["market"])
    with caplog.at_level("INFO", logger="market_catalog"):
        outcome = apply_approvals(store)
    assert outcome["KXBTCD-series-approval-20261013"] == {"status": "bound", "templates": 1,
                                                          "new": 1}
    # Exemplars the catalog does not hold are refused and retried next start, not raised.
    assert outcome["KXWTI-series-approval-20261013"]["status"] == "refused"
    assert "catalog_template_approvals=" in caplog.text
    assert apply_approvals(store)["KXBTCD-series-approval-20261013"]["new"] == 0
    assert len(store.series_template_reviews("KXBTCD")) == 1
    # The same approval posted from the dashboard is the same review, not a second one.
    store.review(APPROVED["KXBTCD"])
    assert len(store.series_template_reviews("KXBTCD")) == 1
    posted = {i["approval"]["ticker"]: i["posted"] for i in recorded_approvals(store)}
    assert posted["KXBTCD"] and not posted["KXWTI"]


def test_whitespace_empty_text_and_title_dates_do_not_split_wording():
    base = raw("KXWTI-26OCT1314-T95.49")
    assert templates.template_hash({**base, "rules_secondary": base["rules_secondary"] + " "}) == (
        templates.template_hash(base))
    assert templates.template_hash({**base, "early_close_condition": ""}) == (
        templates.template_hash({**base, "early_close_condition": None}))
    # A market titled "... Oct 2 ..." must not turn the rule's "2 business days" into a strike.
    dated = {**base, "title": "WTI oil price on Oct 2, 2026?"}
    assert "2 business days" in templates.template_document(dated)["text"]["rules_secondary"]
    assert templates.template_hash(dated) == templates.template_hash(base)


def test_coverage_says_what_differs_and_ignores_older_template_methods(store):
    for ticker in ("KXBTCD-26OCT1617-T87999.99", "KXBTCD-26JUL1317-T62999.99"):
        put(store, ticker)
    other = raw("KXBTCD-26OCT1617-T87999.99")
    other["rules_secondary"] = "An older wording version."
    store.upsert("market", "KXBTCD-26AUG0117-T63749.99", other, "KXBTCD")
    store.evidence_page("live", [live(1, "KXBTCD-26AUG0117-T63749.99")], {"after": 0})
    store.review(approval("KXBTCD"))
    [group] = store.template_coverage("KXBTCD")["unreviewed_templates"]
    [diff] = group["differs_from_approved"]
    assert diff["field"] == "text.rules_secondary"
    assert diff["unapproved"] == "<name> older wording version."
    # A row bound under an earlier template method stays stored but is not listed or matched.
    with store.connect() as db:
        db.execute("UPDATE reviews SET document=? WHERE kind='series_template'",
                   (json.dumps({"template_method": "rules-template-v1", "approval_id": "x"}),))
    assert store.series_template_reviews("KXBTCD") == []
