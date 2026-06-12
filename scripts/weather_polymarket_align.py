"""Polymarket alignment probe — STEP 1 of the lead-lag study (research only).

Before collecting paired price paths it's worth knowing the make-or-break fact: are
Polymarket's temperature markets even the SAME BET as Kalshi's? A lead-lag signal is
meaningless if the two contracts differ in station, bucket boundaries, or close time.

This hits Polymarket's public Gamma API (no auth) from the ops runner's open internet,
finds open temperature events for our cities, and characterizes each against the known
Kalshi structure (settlement station + 2-degree integer buckets). It writes nothing and
trades nothing — it just reports how many city-days are cleanly comparable.

Output per matched city: Polymarket bucket scheme (width, count), close time, and a
resolution-source snippet, vs Kalshi's station, with a heuristic ALIGNED/PARTIAL/MISMATCH
verdict for human judgement.

Usage (ops runner has open internet; no DB needed):
    {"type": "script", "name": "weather_polymarket_align"}
    python scripts/weather_polymarket_align.py --hours-ahead 72
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"
UA = (  # Gamma sits behind Cloudflare; a browser UA avoids the default-urllib block
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Kalshi reference (mirrors kalshi_bot/weather/cities.py): name aliases + settlement
# station + the fact that Kalshi uses 2-integer buckets ("74° to 75°").
KALSHI_CITIES = {
    "NYC": (("new york", "nyc"), "KNYC (Central Park)"),
    "CHI": (("chicago",), "KMDW (Midway)"),
    "MIA": (("miami",), "KMIA"),
    "AUS": (("austin",), "KAUS (Bergstrom)"),
    "LAX": (("los angeles", "lax"), "KLAX"),
    "PHIL": (("philadelphia", "philly"), "KPHL"),
    "DEN": (("denver",), "KDEN"),
}

_MONTHS = {
    m: i + 1 for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"]
    )
}
# Unsigned: the hyphen in a range label ("75-76") must not be read as a minus sign.
_NUM = re.compile(r"\d+(?:\.\d+)?")
_DATE = re.compile(r"\b([a-z]+)\s+(\d{1,2})\b", re.I)


def match_city(text: str) -> str | None:
    low = (text or "").lower()
    for code, (aliases, _station) in KALSHI_CITIES.items():
        if any(a in low for a in aliases):
            return code
    return None


def parse_kind(text: str) -> str | None:
    low = (text or "").lower()
    if "highest" in low or "high temp" in low:
        return "high"
    if "lowest" in low or "low temp" in low:
        return "low"
    return None


def parse_date(text: str, *, now: datetime | None = None) -> str | None:
    """ISO date from 'on June 12' style titles; rolls to next year if it lands far past."""
    now = now or datetime.now(timezone.utc)
    m = _DATE.search(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    day = int(m.group(2))
    try:
        d = datetime(now.year, month, day, tzinfo=timezone.utc).date()
    except ValueError:
        return None
    # Daily markets are always today/tomorrow, so a parsed date more than a couple days
    # in the past means the title's month has wrapped into next year.
    if (now.date() - d).days > 2:
        try:
            d = datetime(now.year + 1, month, day, tzinfo=timezone.utc).date()
        except ValueError:
            return None
    return d.isoformat()


def parse_bucket(label: str) -> tuple[float | None, float | None] | None:
    """Polymarket bucket label -> (low, high). Open ends -> None on that side."""
    if not label:
        return None
    low = label.lower()
    nums = [float(x) for x in _NUM.findall(label)]
    if not nums:
        return None
    if any(w in low for w in ("or above", "or higher", "≥", ">=", "above", "or more")):
        return (nums[0], None)
    if any(w in low for w in ("or below", "or lower", "≤", "<=", "below", "or less", "under")):
        return (None, nums[0])
    if len(nums) >= 2:
        return (min(nums[:2]), max(nums[:2]))
    return (nums[0], nums[0])


def bucket_widths(buckets: list[tuple[float | None, float | None]]) -> list[float]:
    return [hi - lo for lo, hi in buckets if lo is not None and hi is not None]


def resolution_snippet(description: str | None) -> str:
    """Pull the sentence naming the settlement source/station from the rules text."""
    if not description:
        return ""
    for sent in re.split(r"(?<=[.!?])\s+", description):
        low = sent.lower()
        if any(w in low for w in (
            "weather service", "nws", "airport", "station", "according to",
            "as reported", "resolve", "noaa", "climate",
        )):
            return sent.strip()[:240]
    return description.strip()[:240]


def summarize_event(event: dict, *, now: datetime | None = None) -> dict | None:
    title = event.get("title") or ""
    city = match_city(title)
    kind = parse_kind(title)
    if city is None or kind is None:
        return None
    markets = event.get("markets") or []
    buckets, labels = [], []
    description = ""
    for mk in markets:
        label = mk.get("groupItemTitle") or mk.get("question") or ""
        rng = parse_bucket(label)
        if rng is not None:
            buckets.append(rng)
            labels.append(label.strip())
        description = description or mk.get("description") or ""
    widths = bucket_widths(buckets)
    return {
        "city": city,
        "kind": kind,
        "date": parse_date(title, now=now),
        "title": title,
        "slug": event.get("slug"),
        "close": event.get("endDate"),
        "n_buckets": len(buckets),
        "labels": labels,
        "widths": sorted(set(widths)),
        "open_ended": sum(1 for lo, hi in buckets if lo is None or hi is None),
        "source": resolution_snippet(description),
    }


def verdict(summary: dict) -> str:
    """Heuristic comparability vs Kalshi (2-integer buckets at a fixed station)."""
    widths = summary["widths"]
    if summary["n_buckets"] < 2:
        return "MISMATCH (no bucket ladder found)"
    # Kalshi "74° to 75°" spans 1.0 in numeric label terms; allow 1-2.
    bucketed = widths and all(0.9 <= w <= 2.1 for w in widths)
    src = summary["source"].lower()
    station_named = any(s in src for s in ("airport", "central park", "station", "nws", "weather service"))
    if bucketed and station_named:
        return "ALIGNED? (bucket ladder + named source — verify station matches Kalshi)"
    if bucketed:
        return "PARTIAL (bucket ladder matches; settlement source unclear — check station)"
    return "MISMATCH (threshold/range scheme differs from Kalshi 2-deg buckets)"


# --- network ----------------------------------------------------------------------


def _get(path: str, params: dict, *, timeout: float = 25.0) -> list | dict:
    query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{GAMMA}{path}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    last = None
    for _attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
    raise RuntimeError(f"gamma fetch failed for {url}: {last}")


LIMIT = 100  # Gamma caps page size at 100
TAG_HINTS = ("temperature", "weather", "climate", "highest-temp", "lowest-temp")


def find_weather_tags() -> list[dict]:
    """Tags whose slug/label looks weather/temperature related (id + slug for querying)."""
    try:
        tags = _get("/tags", {"limit": 1000})
    except Exception:  # noqa: BLE001
        return []
    items = tags if isinstance(tags, list) else tags.get("data") or []
    found = []
    for t in items:
        slug = (t.get("slug") or "").lower()
        label = (t.get("label") or t.get("name") or "").lower()
        if any(h in slug or h in label for h in TAG_HINTS):
            found.append({"id": t.get("id"), "slug": t.get("slug"), "label": t.get("label")})
    return found


def _events_for(params: dict, max_pages: int, now: datetime, diag: dict) -> list[dict]:
    out: list[dict] = []
    for page in range(max_pages):
        batch = _get("/events", {**params, "limit": LIMIT, "offset": page * LIMIT})
        if not isinstance(batch, list) or not batch:
            break
        diag["events_scanned"] += len(batch)
        for ev in batch:
            title = ev.get("title") or ""
            if "temp" in title.lower() and len(diag["temp_titles"]) < 25:
                diag["temp_titles"].append(title)
            elif len(diag["sample_titles"]) < 12:
                diag["sample_titles"].append(title)
            s = summarize_event(ev, now=now)
            if s is not None:
                out.append(s)
        if len(batch) < LIMIT:
            break
    return out


def fetch_temp_events(hours_ahead: float, max_pages: int) -> tuple[list[dict], dict]:
    """Discover open temperature events via Polymarket TAGS (a blind scan drowns in
    crypto 5-minute markets). Returns matches + diagnostics."""
    now = datetime.now(timezone.utc)
    diag: dict = {"events_scanned": 0, "weather_tags": [], "strategy": None,
                  "temp_titles": [], "sample_titles": []}

    tags = find_weather_tags()
    diag["weather_tags"] = [f"{t['slug']}(id={t['id']})" for t in tags]

    seen: set[str] = set()
    out: list[dict] = []

    def _collect(found: list[dict], strategy: str) -> None:
        for s in found:
            key = s.get("slug") or s["title"]
            if key not in seen:
                seen.add(key)
                out.append(s)
        if found and diag["strategy"] is None:
            diag["strategy"] = strategy

    # 1) query events under each discovered weather tag (by id, then slug)
    for t in tags:
        if t.get("id") is not None:
            _collect(_events_for({"closed": "false", "tag_id": t["id"]}, max_pages, now, diag),
                     f"tag_id={t['id']}")
        if not out and t.get("slug"):
            _collect(_events_for({"closed": "false", "tag_slug": t["slug"]}, max_pages, now, diag),
                     f"tag_slug={t['slug']}")
    # 2) fallback: try the hint slugs directly as tag_slug
    if not out:
        for slug in TAG_HINTS:
            _collect(_events_for({"closed": "false", "tag_slug": slug}, 2, now, diag),
                     f"tag_slug={slug}")
    return out, diag


def report(summaries: list[dict], hours_ahead: float, diag: dict) -> None:
    print("=== Polymarket alignment probe (read-only; lead-lag study step 1) ===")
    print(f"  weather/temp tags found: {', '.join(diag['weather_tags']) or '(none)'}")
    print(f"  events scanned under those tags: {diag['events_scanned']}"
          f"  | winning query: {diag['strategy'] or '(none matched)'}")
    print(f"  matched temperature events for our cities: {len(summaries)}")
    if not summaries:
        print("  --- diagnostics (why zero matches) ---")
        print(f"  titles containing 'temp' seen: {len(diag['temp_titles'])}")
        for t in diag["temp_titles"][:25]:
            print(f"    temp> {t}")
        if not diag["temp_titles"]:
            print("  sample of titles the tag query returned:")
            for t in diag["sample_titles"]:
                print(f"    any> {t}")
        return

    by_city: dict[str, list[dict]] = {}
    for s in summaries:
        by_city.setdefault(s["city"], []).append(s)

    aligned = partial = mismatch = 0
    for city in sorted(by_city):
        _aliases, station = KALSHI_CITIES[city]
        print(f"\n  {city}  (Kalshi station {station}, 2-degree buckets)")
        for s in sorted(by_city[city], key=lambda x: (x["kind"], x["date"] or "")):
            v = verdict(s)
            aligned += v.startswith("ALIGNED")
            partial += v.startswith("PARTIAL")
            mismatch += v.startswith("MISMATCH")
            widths = "/".join(f"{w:g}" for w in s["widths"]) or "n/a"
            print(f"    [{s['kind']:>4}] {s['date'] or '?'}  buckets={s['n_buckets']:>2}"
                  f" width={widths} open_ended={s['open_ended']}  close={s['close']}")
            if s["labels"]:
                print(f"           labels: {', '.join(s['labels'][:6])}"
                      + (" ..." if len(s["labels"]) > 6 else ""))
            if s["source"]:
                print(f"           PM source: {s['source']}")
            print(f"           -> {v}")
    print(f"\n  verdict tally: ALIGNED?={aligned}  PARTIAL={partial}  MISMATCH={mismatch}")
    print("  (ALIGNED?/PARTIAL still need a human to confirm the settlement STATION matches —")
    print("   that's the one thing that decides whether a lead-lag signal is real.)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours-ahead", type=float, default=72.0,
                    help="only scan open events closing within this many hours")
    ap.add_argument("--max-pages", type=int, default=10)
    args = ap.parse_args(argv)
    try:
        summaries, diag = fetch_temp_events(args.hours_ahead, args.max_pages)
    except Exception as exc:  # noqa: BLE001 — probe: report the failure, don't crash the runner
        print(f"=== Polymarket alignment probe ===\n  fetch failed: {exc}", file=sys.stderr)
        return 1
    report(summaries, args.hours_ahead, diag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
