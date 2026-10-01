"""DEC-024 open-internet evidence: SSRF refusal, redirects, storage and provenance."""
import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from test_desks_research import NOW, OUTPUT, runtime, store_at

from kalshi_bot.desks.contracts import Decision, DeskError
from kalshi_bot.desks.web import PublicFetcher, address_allowed, check_url

PUBLIC = "93.184.216.34"


def fetcher(handler, resolver=None, **kwargs):
    return PublicFetcher(resolver=resolver or (lambda host: [PUBLIC]),
                         transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "255.255.255.255", "192.0.2.10", "198.18.0.1", "::1", "::",
    "fe80::1", "fc00::1", "fd00:ec2::254", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
    "2002:7f00:1::1", "64:ff9b::a9fe:a9fe", "2001:db8::1", "ff02::1", "not-an-ip"])
def test_non_global_addresses_are_refused(address):
    assert not address_allowed(address)


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_global_addresses_are_allowed(address):
    assert address_allowed(address)


@pytest.mark.parametrize("url,code", [
    ("http://example.com/a", "source_url_refused"),
    ("https://example.com:8443/a", "source_url_refused"),
    ("https://user:pw@example.com/a", "source_url_refused"),
    ("ftp://example.com/a", "source_url_refused"),
    ("https://localhost/a", "source_address_refused"),
    ("https://metadata.google.internal/a", "source_address_refused"),
    ("https://printer.local/a", "source_address_refused"),
    ("https://intranet/a", "source_address_refused"),
    ("https://169.254.169.254/latest/meta-data", "source_address_refused"),
    ("https://[::1]/a", "source_address_refused"),
    ("https://[fd00::1]/a", "source_address_refused"),
    ("https://example.com/" + "a" * 2100, "source_url_invalid"),
    ("https://exa mple.com/", "source_url_invalid"),
])
def test_url_shape_refusals(url, code):
    with pytest.raises(DeskError, match=code):
        check_url(url)


def test_any_public_host_is_captured_pinned_to_the_checked_address_without_credentials():
    seen = []
    body = "<html><head><script>steal()</script><style>x{}</style></head><body><h1>Weekly report</h1>" \
           + "<p>Value &amp; trend: 7.12%</p>" + "<p>row</p>" * 5000 + "<p>END-MARKER</p></body></html>"

    def handle(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=body)
    source = fetcher(handle)("https://www.some-random-blog.example.org/post?id=1#frag", NOW)
    request = seen[0]
    assert request.url.host == PUBLIC
    assert request.headers["host"] == "www.some-random-blog.example.org"
    assert request.extensions["sni_hostname"] == "www.some-random-blog.example.org"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert source["url"] == "https://www.some-random-blog.example.org/post?id=1#frag"
    assert "steal()" not in source["excerpt"] and "Value & trend: 7.12%" in source["excerpt"]
    assert source["excerpt"].endswith("END-MARKER")  # long pages are stored whole, not cut at 12k
    assert source["chars"] > 12000 and not source["truncated"]
    assert source["sha256"] == hashlib.sha256(source["excerpt"].encode()).hexdigest()


@pytest.mark.parametrize("answers", [["10.0.0.5"], [PUBLIC, "127.0.0.1"], ["fd12::1"], []])
def test_dns_answers_with_any_private_address_are_refused_before_connecting(answers):
    calls = []
    fetch = fetcher(lambda request: calls.append(request) or httpx.Response(200, text="x"),
                    resolver=lambda host: answers)
    with pytest.raises(DeskError, match="source_address_refused"):
        fetch("https://rebind.example.com/a", NOW)
    assert not calls


def test_redirects_are_rechecked_on_every_hop():
    def to(location):
        return lambda request: httpx.Response(302, headers={"location": location})
    with pytest.raises(DeskError, match="source_address_refused"):
        fetcher(to("https://169.254.169.254/latest/meta-data/"))("https://example.com/a", NOW)
    with pytest.raises(DeskError, match="source_url_refused"):
        fetcher(to("http://example.com/b"))("https://example.com/a", NOW)
    hosts = {"example.com": [PUBLIC], "inside.example.net": ["10.9.8.7"]}
    with pytest.raises(DeskError, match="source_address_refused"):
        fetcher(to("https://inside.example.net/x"), resolver=lambda h: hosts[h])("https://example.com/a", NOW)
    with pytest.raises(DeskError, match="source_too_many_redirects"):
        fetcher(to("/again"))("https://example.com/a", NOW)


def test_public_redirect_is_followed_without_forwarding_cookies():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://other.example.net/final",
                                                "set-cookie": "session=secret"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="final body")
    source = fetcher(handle)("https://example.com/start", NOW)
    assert [r.headers["host"] for r in seen] == ["example.com", "other.example.net"]
    assert "cookie" not in seen[1].headers
    assert source["final_url"] == "https://other.example.net/final"
    assert source["redirects"] == ["https://other.example.net/final"]
    assert source["url"] == "https://example.com/start" and source["excerpt"] == "final body"


def test_size_content_type_and_status_limits():
    big = fetcher(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text="x" * 5000),
                  max_bytes=1000)
    with pytest.raises(DeskError, match="source_too_large"):
        big("https://example.com/a", NOW)
    pdf = fetcher(lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    with pytest.raises(DeskError, match="source_pdf_unsupported"):
        pdf("https://example.com/a.pdf", NOW)
    image = fetcher(lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG"))
    with pytest.raises(DeskError, match="source_content_type_unsupported"):
        image("https://example.com/a.png", NOW)
    missing = fetcher(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(DeskError, match="source_http_failure"):
        missing("https://example.com/a", NOW)
    capped = fetcher(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, text="y" * 500),
                     max_chars=100)("https://example.com/a", NOW)
    assert capped["truncated"] and capped["chars"] == 100


def test_kalshi_json_lists_are_stored_whole_and_parsed():
    markets = [{"ticker": f"KXTEST-26OCT01-B{i}", "event_ticker": "KXTEST-26OCT01",
                "rules_primary": "If the value is above " + str(i) + " the market resolves Yes."}
               for i in range(300)]
    raw = json.dumps({"markets": markets, "cursor": "next"})
    assert len(raw) > 12000
    fetch = fetcher(lambda r: httpx.Response(200, headers={"content-type": "application/json"}, text=raw))
    source = fetch("https://api.elections.kalshi.com/trade-api/v2/markets?event_ticker=KXTEST-26OCT01", NOW)
    assert source["excerpt"] == raw and json.loads(source["excerpt"])["markets"][-1]["ticker"].endswith("B299")
    assert len(source["_market_data"]) == 300


PAGE = "https://data.example-agency.gov/weekly/report.html"
SETTLE = "https://settlement.example-agency.gov/official.json"


def capture_supervisor(tmp_path, **kwargs):
    pages = {"/weekly/report.html": ("text/html", "<p>Weekly index printed at 412.7 on Tuesday.</p>"),
             "/official.json": ("application/json", '{"official": true}')}

    def handle(request):
        kind, text = pages[request.url.path]
        return httpx.Response(200, headers={"content-type": kind}, text=text)
    store = store_at(tmp_path)
    return runtime(store, research_mode="session", source_fetcher=fetcher(handle), **kwargs)


def decision(source, excerpt, now, settlement=SETTLE):
    rules = "Settles on the official index value published by the agency."
    return Decision(
        decision_id="web-evidence-decision", desk_id="chatgpt", round_id="research-test",
        ticker="KXINDEX-T400", event_id="KXINDEX", side="yes", observed_price=".40", quote_at=now,
        max_price=".40", max_spend="1", probability=".8", probability_low=".7", probability_high=".9",
        expected_net_profit=".76", settlement_source=settlement, settlement_rule=rules,
        rules_sha256=hashlib.sha256(rules.encode()).hexdigest(),
        thesis="The published weekly index already exceeds the strike.",
        counterargument="A revision before settlement could lower the print.",
        invalidation="Agency revises the value below 400.", edge_class="information",
        evidence=[{"url": source["url"], "retrieved_at": source["retrieved_at"], "excerpt": excerpt,
                   "sha256": source["sha256"]}],
        created_at=now, expires_at=now + timedelta(minutes=5), author_model="offline", origin="session")


def test_decision_citing_an_arbitrary_captured_page_verifies_and_tampering_is_refused(tmp_path):
    sup = capture_supervisor(tmp_path)
    job = sup.claim_external("chatgpt", "app", NOW)
    page = sup.fetch_external_source(job["job_id"], job["claim_token"], "chatgpt", PAGE, NOW)
    sup.fetch_external_source(job["job_id"], job["claim_token"], "chatgpt", SETTLE, NOW)
    sup.verify_decision_sources(decision(page, "index printed at 412.7", NOW))
    with pytest.raises(DeskError, match="unverified_decision_evidence"):
        sup.verify_decision_sources(decision(page, "index printed at 512.7", NOW))
    with pytest.raises(DeskError, match="unverified_decision_evidence"):
        sup.verify_decision_sources(decision({**page, "sha256": "b" * 64}, "index printed at 412.7", NOW))
    with pytest.raises(DeskError, match="unverified_settlement_source"):
        sup.verify_decision_sources(decision(page, "index printed at 412.7", NOW,
                                             settlement="https://uncaptured.example.com/x"))


def test_capture_limit_is_generous_by_default_and_configurable(tmp_path):
    sup = capture_supervisor(tmp_path, max_sources_per_job=3)
    job = sup.claim_external("chatgpt", "app", NOW)
    assert job["context"]["research_tools"]["max_source_captures"] == 3
    for _ in range(3):
        sup.fetch_external_source(job["job_id"], job["claim_token"], "chatgpt", PAGE, NOW)
    with pytest.raises(DeskError, match="source_request_limit"):
        sup.fetch_external_source(job["job_id"], job["claim_token"], "chatgpt", PAGE, NOW)
    sup.complete_external(job["job_id"], job["claim_token"], OUTPUT, "model", NOW, desk_id="chatgpt")
    (tmp_path / "other").mkdir()
    (tmp_path / "bad").mkdir()
    default = runtime(store_at(tmp_path / "other"))
    assert default.max_sources == 50 and default.lease == timedelta(minutes=60)
    with pytest.raises(ValueError):
        runtime(store_at(tmp_path / "bad"), max_sources_per_job=0)


def test_board_tools_hint_marks_browse_as_discovery_not_evidence(tmp_path):
    sup = capture_supervisor(tmp_path)
    tools = sup.claim_external("chatgpt", "app", NOW)["context"]["research_tools"]
    assert tools["browse_is_evidence"] is False and "markets" in tools["market_browse"]
