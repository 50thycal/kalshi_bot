"""catalog_review_sources — public, read-only, two hosts; rules digest matches the catalog's."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import catalog_review_sources as crs  # noqa: E402

from kalshi_bot.catalog.store import RULE_FIELDS, rule_hash  # noqa: E402


def test_allowlisted_on_the_ops_channel():
    import ops_runner

    assert "catalog_review_sources" in ops_runner.ALLOWED_SCRIPTS


def test_rule_fields_and_fingerprint_match_the_catalog():
    assert crs.RULE_FIELDS == RULE_FIELDS
    market = {"rules_primary": "If above 1, Yes.", "title": "T", "yes_bid_dollars": "0.4"}
    assert crs.rules_fingerprint(market) == rule_hash(market)


def test_only_https_on_the_two_hosts():
    assert crs.host_allowed("https://api.elections.kalshi.com/trade-api/v2/markets/X")
    assert crs.host_allowed("https://assets.kalshi.com/contract_terms/BTC.pdf")
    assert not crs.host_allowed("http://assets.kalshi.com/contract_terms/BTC.pdf")
    assert not crs.host_allowed("https://assets.kalshi.com.evil.example/x.pdf")
    assert not crs.host_allowed("https://example.com/")


def test_documents_outside_the_catalog_scope_are_not_fetched(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("fetch must not be called")

    monkeypatch.setattr(crs, "fetch", boom)
    assert crs.document("https://assets.kalshi.com/other/x.pdf", 100)["status"] == "unsupported_url"


def test_bad_tickers_are_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(crs, "get_json", lambda path: (_ for _ in ()).throw(AssertionError))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert crs.main(["../etc/passwd"]) == 1
    assert "refused" in err.getvalue()


def _pdf(text_ops: bytes) -> bytes:
    stream = zlib.compress(text_ops)
    return (b"%%PDF-1.4\n1 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
            + stream + b"\nendstream\nendobj\n/CreationDate (D:20260908162248-04'00')\n%%EOF")


def test_flate_text_is_extracted_and_quality_reported():
    ops = (b"BT /F1 12 Tf (The market resolves to Yes if the official value is above the) Tj "
           b"T* [(strike) -250 (on the) -250 (date) ] TJ T* (published by the agency \\(see "
           b"Appendix\\) and the source agency will be the governing statistics bureau.) Tj ET")
    text, quality = crs.pdf_text(_pdf(ops))
    assert quality == "readable"
    assert "resolves to Yes" in text and "strikeon thedate" in text and "(see Appendix)" in text
    assert crs.pdf_metadata(_pdf(ops))["CreationDate"].startswith("D:2026")
    assert crs.pdf_text(_pdf(b"BT <0012003A> Tj ET"))[1] == "unavailable"


def test_report_shape_with_mocked_api(monkeypatch):
    market = {"ticker": "KXA-E-T1", "event_ticker": "KXA-E", "rules_primary": "r",
              "contract_terms_url": "https://assets.kalshi.com/contract_terms/A.pdf",
              "status": "finalized", "result": "no"}
    payloads = {"/markets/KXA-E-T1": {"market": market},
                "/events/KXA-E": {"event": {"event_ticker": "KXA-E", "series_ticker": "KXA",
                                            "mutually_exclusive": False}},
                "/series/KXA": {"series": {"ticker": "KXA", "settlement_sources": [
                    {"name": "Agency", "url": "https://agency.example"}]}}}
    monkeypatch.setattr(crs, "get_json", payloads.get)
    pdf = _pdf(b"BT (" + b"Official terms describe settlement by the named agency. " * 3 + b") Tj ET")
    monkeypatch.setattr(crs, "fetch", lambda url, **_k: (200, {"Last-Modified": "x"}, pdf))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert crs.main(["KXA-E-T1", "--pdf-chars", "40"]) == 0
    report = json.loads(out.getvalue())
    [m] = report["markets"]
    assert m["public_rules_fingerprint"] == rule_hash(market)
    assert m["series"]["settlement_sources"][0]["name"] == "Agency"
    [doc] = report["documents"]
    assert doc["byte_count"] == len(pdf) and len(doc["text"]) == 40 and doc["text_truncated"]
    assert doc["version_at_execution_verified"] is False
    assert report["retrieval_time_is_not_effective_date"] is True
