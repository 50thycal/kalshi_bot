"""Catalog review sources — official public rules and contract documents for review drafting.

Exact-rule reviews (WS-023) must quote the listing's own rules and the contract terms the
listing links to. The Claude sandbox cannot reach Kalshi; the ops runner can. For each market
ticker this prints, from Kalshi's PUBLIC market-data API only:

  * the market's rule fields (the same fields the catalog's rules hash reads), timing and
    settlement facts, and SHA-256 fingerprints of the canonical market/event/series payloads;
  * the parent event's grouping fields and the series' settlement sources and document links;
  * for each linked contract PDF on assets.kalshi.com (deduplicated across tickers): byte
    count, SHA-256, Last-Modified, PDF creation metadata and best-effort extracted text.

Read-only: GETs only, no credentials, two fixed hosts. Retrieval time and PDF metadata are
NOT proof of which terms governed an earlier execution; the output says so. Text extraction is
stdlib best-effort and reports when it fails rather than guessing.

With --pdf-base64 each retrieved PDF's exact bytes are included (base64) so a full PDF reader
can be used offline; --terms-only skips the larger product-certification documents.

Usage (via the ops channel):
    {"type":"script","name":"catalog_review_sources",
     "args":["KXBTCD-26AUG0117-T63749.99","KXRAIN-26AUG01-T0","--pdf-chars","8000"]}
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone

API = "https://api.elections.kalshi.com/trade-api/v2"
ALLOWED_HOSTS = ("api.elections.kalshi.com", "assets.kalshi.com")
# Same document scope the catalog archives (kalshi_bot/catalog/documents.py URL_PATTERN).
PDF_URL = re.compile(
    r"https://assets\.kalshi\.com/(?:contract_terms|regulatory/product-certifications)/"
    r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.pdf\Z"
)
TICKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
# Mirrors kalshi_bot.catalog.store.RULE_FIELDS (pinned by a test), so the printed public
# fingerprint is the same rules digest the catalog computes before parent binding.
RULE_FIELDS = (
    "rules_primary", "rules_secondary", "early_close_condition", "can_close_early",
    "settlement_timer_seconds", "strike_type", "floor_strike", "cap_strike",
    "functional_strike", "custom_strike", "title", "yes_sub_title", "no_sub_title",
    "open_time", "close_time", "expected_expiration_time", "settlement_sources",
    "contract_url", "contract_terms_url", "fee_type", "fee_multiplier", "mutually_exclusive",
)
MARKET_FACTS = (
    "ticker", "event_ticker", "market_type", "status", "result", "settlement_ts",
    "settlement_value_dollars", "expiration_value", "expiration_time", "latest_expiration_time",
    "notional_value_dollars", "subtitle", "price_level_structure", "is_provisional",
)
EVENT_FIELDS = (
    "event_ticker", "series_ticker", "title", "sub_title", "mutually_exclusive", "category",
    "strike_date", "strike_period",
)
SERIES_FIELDS = (
    "ticker", "title", "category", "frequency", "settlement_sources", "contract_url",
    "contract_terms_url", "fee_type", "fee_multiplier", "tags", "additional_prohibitions",
)
MAX_TICKERS = 20
MAX_PDF_BYTES = 4 * 1024 * 1024
PDF_CHARS_CAP = 15_000
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def host_allowed(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_HOSTS


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def rules_fingerprint(market: dict) -> str:
    return digest({key: market.get(key) for key in RULE_FIELDS})


def fetch(url: str, limit: int = MAX_PDF_BYTES, timeout: int = 30) -> tuple[int, dict, bytes]:
    if not host_allowed(url):
        raise ValueError("host not allowlisted")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*",
                                               "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (allowlisted)
            body = resp.read(limit + 1)
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), b""


def get_json(path: str) -> dict | None:
    status, _headers, body = fetch(API + path, limit=2 * 1024 * 1024)
    if status != 200:
        return None
    return json.loads(body)


_STRING = re.compile(rb"\((?:\\.|[^\\)])*\)")
_ARRAY = re.compile(rb"\[((?:\\.|[^\]])*)\]\s*TJ")
_ESCAPES = {b"n": b"\n", b"r": b"", b"t": b" ", b"b": b"", b"f": b"", b"(": b"(", b")": b")",
            b"\\": b"\\"}


def _unescape(raw: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(raw):
        ch = raw[i:i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(), 8) & 0xFF)
                i += 1 + len(octal.group())
                continue
            i += 1
            continue
        out += ch
        i += 1
    return bytes(out)


def _content_text(stream: bytes) -> str:
    parts = []
    for line in re.split(rb"(?:\bT\*|\bTd\b|\bTD\b|\bET\b|')", stream):
        pieces = []
        for array in _ARRAY.findall(line):
            pieces += [_unescape(s[1:-1]) for s in _STRING.findall(array)]
        for match in re.finditer(rb"(\((?:\\.|[^\\)])*\))\s*Tj", line):
            pieces.append(_unescape(match.group(1)[1:-1]))
        if pieces:
            parts.append(b"".join(pieces).decode("latin-1"))
    return "\n".join(parts)


def pdf_text(body: bytes) -> tuple[str, str]:
    """Best-effort text from FlateDecode content streams. Returns (text, quality)."""
    texts = []
    for match in re.finditer(rb"<<(.{0,800}?)>>\s*stream\r?\n(.*?)\r?\nendstream", body, re.S):
        header, data = match.group(1), match.group(2)
        if b"/FlateDecode" in header:
            try:
                data = zlib.decompress(data)
            except zlib.error:
                continue
        elif b"/Filter" in header:
            continue
        if b"Tj" in data or b"TJ" in data:
            texts.append(_content_text(data))
    text = re.sub(r"[ \t]+", " ", "\n".join(t for t in texts if t.strip())).strip()
    if not text:
        return "", "unavailable"
    letters = sum(c.isalpha() for c in text)
    words = len(re.findall(r"[A-Za-z]{3,}", text))
    readable = letters / max(1, len(text)) > 0.5 and words >= 20
    return text, "readable" if readable else "garbled_or_encoded_fonts"


def pdf_metadata(body: bytes) -> dict:
    found = {}
    for key in ("CreationDate", "ModDate", "Producer", "Title"):
        match = re.search(rb"/" + key.encode() + rb"\s*\(([^)]{0,120})\)", body)
        if match:
            found[key] = match.group(1).decode("latin-1")
    return found


def document(url: str, chars: int, raw: bool = False) -> dict:
    if not PDF_URL.fullmatch(url or ""):
        return {"url": url, "status": "unsupported_url"}
    status, headers, body = fetch(url)
    if status != 200:
        return {"url": url, "status": f"http_{status}"}
    if len(body) > MAX_PDF_BYTES:
        return {"url": url, "status": "too_large"}
    if not body.startswith(b"%PDF-"):
        return {"url": url, "status": "not_pdf"}
    text, quality = pdf_text(body)
    return {
        "url": url,
        "status": "retrieved",
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "byte_count": len(body),
        "last_modified": headers.get("Last-Modified"),
        "etag": headers.get("ETag"),
        "pdf_metadata": pdf_metadata(body),
        "text_quality": quality,
        "text_chars_total": len(text),
        "text": text[:chars] if quality == "readable" else None,
        "text_truncated": quality == "readable" and len(text) > chars,
        "metadata_is_effective_date_proof": False,
        "version_at_execution_verified": False,
        # The exact retrieved bytes, so a full PDF reader can extract what this stdlib
        # extractor cannot (encoded fonts). Same bytes as content_sha256.
        "content_base64": base64.b64encode(body).decode() if raw else None,
    }


def market_sources(ticker: str) -> dict:
    data = get_json(f"/markets/{urllib.parse.quote(ticker, safe='')}")
    endpoint = "live"
    if data is None:
        data = get_json(f"/historical/markets/{urllib.parse.quote(ticker, safe='')}")
        endpoint = "historical"
    if data is None:
        return {"ticker": ticker, "status": "not_found_live_or_historical"}
    market = data.get("market") or data
    event_ticker = market.get("event_ticker") or ticker.rsplit("-", 1)[0]
    event_data = get_json(f"/events/{urllib.parse.quote(event_ticker, safe='')}") or {}
    event = event_data.get("event") or {}
    series_ticker = event.get("series_ticker") or ticker.split("-", 1)[0]
    series_data = get_json(f"/series/{urllib.parse.quote(series_ticker, safe='')}") or {}
    series = series_data.get("series") or {}
    return {
        "ticker": ticker,
        "status": "retrieved",
        "endpoint": endpoint,
        "public_rules_fingerprint": rules_fingerprint(market),
        "public_payload_fingerprints": {
            "market": digest(market), "event": digest(event) if event else None,
            "series": digest(series) if series else None,
        },
        "rules": {key: market.get(key) for key in RULE_FIELDS},
        "market_facts": {key: market.get(key) for key in MARKET_FACTS if key in market},
        "event": {key: event.get(key) for key in EVENT_FIELDS if key in event},
        "series": {key: series.get(key) for key in SERIES_FIELDS if key in series},
        "document_urls": sorted({
            url for url in (
                market.get("contract_url"), market.get("contract_terms_url"),
                series.get("contract_url"), series.get("contract_terms_url"),
            ) if url
        }),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--pdf-chars", type=int, default=6000,
                    help=f"extracted text per document (hard cap {PDF_CHARS_CAP})")
    ap.add_argument("--no-pdf", action="store_true", help="skip contract documents")
    ap.add_argument("--pdf-base64", action="store_true",
                    help="include each retrieved PDF's exact bytes, base64-encoded")
    ap.add_argument("--terms-only", action="store_true",
                    help="contract terms only, not product certifications")
    args = ap.parse_args(argv)
    tickers = list(dict.fromkeys(args.tickers))
    if len(tickers) > MAX_TICKERS or any(not TICKER.fullmatch(t) for t in tickers):
        print(f"refused: 1-{MAX_TICKERS} market tickers of [A-Za-z0-9._-]", file=sys.stderr)
        return 1
    chars = max(0, min(args.pdf_chars, PDF_CHARS_CAP))
    report = {
        "method": "catalog-review-sources-v1",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "retrieval_time_is_not_effective_date": True,
        "markets": [market_sources(ticker) for ticker in tickers],
    }
    urls = sorted({u for m in report["markets"] for u in m.get("document_urls", [])})
    if args.terms_only:
        urls = [u for u in urls if "/contract_terms/" in u]
    report["documents"] = [] if args.no_pdf else [
        document(url, chars, raw=args.pdf_base64) for url in urls
    ]
    print(json.dumps(report, indent=1, sort_keys=True, default=str))
    return 0 if all(m["status"] == "retrieved" for m in report["markets"]) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
