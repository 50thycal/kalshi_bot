"""Desk fetch — read ONE public settlement-source page from the ops runner, bounded.

The Claude sandbox cannot reach the web hosts a discretionary pick has to be checked
against (OpenRouter's rankings, AAA's daily fuel average, the EIA, the Chicago Fed, the
NWS). The ops runner can. This script GETs one URL on a fixed allowlist of PUBLIC hosts,
strips it to text (or pretty-prints JSON), and prints a bounded slice — optionally only the
windows around a search term. No credentials, no cookies, no POST, and the result lands
on the public ops branch, so the allowlist is hosts whose pages are public by nature.

Usage (via the ops channel):
    {"type":"script","name":"desk_fetch","args":["https://gasprices.aaa.com/"]}
    {"type":"script","name":"desk_fetch","args":["https://openrouter.ai/rankings","--find","Anthropic","--window","300"]}
    {"type":"script","name":"desk_fetch","args":["https://api.eia.gov/...","--max-chars","4000"]}

Adding a host is a pull request: the allowlist is the whole security model.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS: tuple[str, ...] = (
    # AI usage / model-share settlement sources
    "openrouter.ai",
    # fuel prices
    "gasprices.aaa.com",
    "www.eia.gov",
    "api.eia.gov",
    # macro releases
    "www.chicagofed.org",
    "fred.stlouisfed.org",
    "api.stlouisfed.org",
    "alfred.stlouisfed.org",
    "www.bls.gov",
    "api.bls.gov",
    "www.census.gov",
    "www.federalreserve.gov",
    "www.bea.gov",
    # weather settlement sources
    "api.weather.gov",
    "forecast.weather.gov",
    "www.weather.gov",
    "api.open-meteo.com",
    "ensemble-api.open-meteo.com",
    # grid load settlement sources
    "www.ercot.com",
    # cross-venue prices (signal only)
    "gamma-api.polymarket.com",
    "clob.polymarket.com",
    # entertainment / streaming settlement sources
    "charts.youtube.com",
    "www.billboard.com",
    "www.boxofficemojo.com",
    # crypto reference prices
    "api.exchange.coinbase.com",
    "api.coinbase.com",
    "benchmarks.pyth.network",
)

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_CHARS_CAP = 20_000  # the ops result is public and bounded; never print more than this


def host_allowed(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_HOSTS


def to_text(body: str, content_type: str) -> str:
    """JSON → pretty JSON; HTML → visible text with scripts/styles dropped; else as is."""
    ct = (content_type or "").lower()
    stripped = body.strip()
    if "json" in ct or stripped[:1] in "{[":
        try:
            return json.dumps(json.loads(stripped), indent=1, sort_keys=True)
        except ValueError:
            pass
    if "html" in ct or "<html" in stripped[:2000].lower():
        text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", body)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
        return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
    return body


def windows(text: str, needle: str, width: int, limit: int) -> list[str]:
    """Up to `limit` windows of ±width chars around each case-insensitive hit of `needle`."""
    out: list[str] = []
    low, nd = text.lower(), needle.lower()
    pos = 0
    while len(out) < limit:
        i = low.find(nd, pos)
        if i < 0:
            break
        out.append(text[max(0, i - width): i + len(nd) + width].replace("\n", " "))
        pos = i + len(nd)
    return out


def fetch(url: str, timeout: int = 45) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (allowlisted host)
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", exc.read().decode("utf-8", "replace")[:2000]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--find", default="", help="print only windows around this term")
    ap.add_argument("--window", type=int, default=200, help="chars each side of a --find hit")
    ap.add_argument("--hits", type=int, default=12, help="max --find windows")
    ap.add_argument("--max-chars", type=int, default=6000, help=f"output cap without --find (hard cap {MAX_CHARS_CAP})")
    ap.add_argument("--raw", action="store_true", help="skip HTML-to-text conversion")
    args = ap.parse_args(argv)

    if not host_allowed(args.url):
        print(f"refused: host not on the desk allowlist ({', '.join(ALLOWED_HOSTS)})", file=sys.stderr)
        return 1
    status, ctype, body = fetch(args.url)
    print(f"# desk_fetch {args.url}\n# HTTP {status} · {ctype or '-'} · {len(body)} bytes")
    if status >= 400:
        print(body[:1000])
        return 1
    text = body if args.raw else to_text(body, ctype)
    if args.find:
        hits = windows(text, args.find, args.window, args.hits)
        print(f"# {len(hits)} window(s) around {args.find!r}")
        for h in hits:
            print("---")
            print(h[:MAX_CHARS_CAP])
        if not hits:
            print("(no hits — the page may be rendered client-side; try --raw or an API URL)")
        return 0
    cap = min(args.max_chars, MAX_CHARS_CAP)
    print(text[:cap])
    if len(text) > cap:
        print(f"\n… [{len(text) - cap} more chars; raise --max-chars (cap {MAX_CHARS_CAP}) or use --find]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
