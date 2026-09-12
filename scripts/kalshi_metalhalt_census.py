"""METALHALT recon census — do Kalshi metals markets exist whose settlement window sits INSIDE
a Pyth feed halt, and do they trade there? (idea-model run 2026-09-12, docs/METALHALT_THESIS.md)

WHY THIS CENSUS EXISTS
----------------------
FREEZE (docs/FREEZE_THESIS.md) is the exchange-closure pin: a hub contract whose remaining
window falls entirely inside a period when the settlement source cannot print is mechanically
decided while retail keeps quoting it cents from certainty. The 2026-07-11 probe EXCLUDED
metals and energy on the premise that "the hub settles on Pyth, which is continuous 24/7", and
the book was later stood down because the grain/soft universe it was pointed at does not exist
(WS-005: "searched by crop name, the wrong axis; it must be searched by settlement SOURCE").

The premise was wrong for metals. Pyth's XAU/USD and XAG/USD feeds publish on metal market
hours — Sunday 18:00 ET to Friday 17:00 ET with a daily 17:00–18:00 ET break — and the 15-minute
gold/silver markets Kalshi launched in August 2026 run around the clock on weekdays. So the
right-axis question WS-005 D1 asks ("is anyone going to look for a qualifying universe?") has a
cheap, concrete answer on this venue: count the settled metals markets whose close falls inside
a halt, and look at what traded there.

This is a CENSUS, not a probe: it decides only whether the full FREEZE probe is worth pointing
at metals. Read-only public Kalshi REST, stdlib only; no DB, no trading.

WHAT IT MEASURES
----------------
  C1 STRUCTURE — settled metals markets (series KXGOLD*/KXSILVER*/KXXAU*/KXXAG*) classified by
     how their window sits against the Pyth halt calendar:
       outside   feed live at close (the normal case; the control population)
       tail      window opened while the feed was live, closed while it was halted —
                 decided from the halt start onward
       inside    entire window inside a halt — decided at the open
       boundary  close_time is exactly a resume instant (18:00 ET weekday / Sunday 18:00 ET):
                 the settlement print may be the FIRST post-resume print, so the outcome is
                 NOT frozen; excluded from the pin population and reported separately (P5 in
                 the thesis — a "pinned" side that lost here is the calendar, not the edge)
  C2 CAPACITY — counts and volume per class; how many settled inside/tail markets exist today
     against the thesis n-floor.
  C3 TAPE — for a sample of settled inside/tail markets, 1-minute candles over the decided
     stretch: did anyone trade after the outcome was fixed, and at what discount to certainty
     (winner's ask below 100¢ / loser's bid above 0¢)? Quotes are candle closes, so this is an
     upper bound on fillability — the full probe scores actual prints.

Verdict: PROMOTE-TO-PROBE if (inside+tail) settled with volume clears the floor AND the sampled
tape shows post-pin trading at a discount; HOLD (universe absent / too new) otherwise. Neither
outcome is a verdict on the FREEZE mechanism — a census prints, a verdict is recorded.

Usage (ops channel):
    {"type":"script","name":"kalshi_metalhalt_census","args":["--max-event-pages","80"],
     "id":"metalhalt-0"}
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Precision over recall: series prefixes only, never title words ("gold" matches Olympics).
METAL_SERIES = re.compile(r"^KX(GOLD|SILVER|XAU|XAG)", re.I)

# Pyth metals market hours (America/New_York): open Sunday 18:00, close Friday 17:00,
# daily break 17:00–18:00 Monday–Thursday. Weekday numbering: Monday=0 … Sunday=6.
HALT_OPEN_HOUR = 17
HALT_CLOSE_HOUR = 18

DISCOUNT_BAR_CENTS = 3.0   # FREEZE's own P1 bar, kept identical for comparability
N_FLOOR = 40               # settled inside+tail markets with volume before a full probe is worth it


def _et():
    """America/New_York tzinfo; falls back to fixed EDT and says so (never silently)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York"), True
    except Exception:  # noqa: BLE001 — tzdata missing on the runner is the only realistic case
        return timezone(timedelta(hours=-4)), False


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _vol(m: dict) -> float:
    return xl._num(m.get("volume_fp")) or xl._num(m.get("volume")) or 0.0


def feed_halted(ts: float, tz) -> bool:
    """True if the Pyth metals feed is halted at the instant `ts` (epoch seconds)."""
    d = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
    wd, h = d.weekday(), d.hour
    if wd == 5:                      # Saturday: closed all day
        return True
    if wd == 6:                      # Sunday: closed until 18:00
        return h < HALT_CLOSE_HOUR
    if wd == 4:                      # Friday: closed from 17:00
        return h >= HALT_OPEN_HOUR
    return HALT_OPEN_HOUR <= h < HALT_CLOSE_HOUR   # Mon–Thu daily break


def is_resume_instant(ts: float, tz) -> bool:
    """close_time lands exactly on a feed resume (18:00 ET Mon–Thu, Sunday 18:00 ET)."""
    d = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
    return d.hour == HALT_CLOSE_HOUR and d.minute == 0 and d.second == 0 and d.weekday() in (
        0, 1, 2, 3, 6)


def classify_window(open_ts: float, close_ts: float, tz) -> tuple[str, float]:
    """(class, decided_from_ts). decided_from_ts is the instant after which the reference price
    can no longer change before close (== close_ts when the window is not pinned)."""
    if not open_ts or not close_ts or close_ts <= open_ts:
        return "unknown", close_ts
    if is_resume_instant(close_ts, tz):
        return "boundary", close_ts
    if not feed_halted(close_ts - 1, tz):
        return "outside", close_ts
    # Walk back minute by minute from close to the halt start (halts are ≥ 1 h, ≤ 49 h).
    t = close_ts - 60
    while t > open_ts and feed_halted(t - 1, tz):
        t -= 60
    decided_from = max(open_ts, t)
    return ("inside" if decided_from <= open_ts + 60 else "tail"), decided_from


def events(status: str, max_pages: int) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status={status}&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    return (data or {}).get("candlesticks") or []


def discount_cents(c: dict, result: str) -> float | None:
    """Discount to certainty implied by the candle's closing quote for the side that WON.
    Winner YES → buy at yes_ask; winner NO → buy NO at (100 − yes_bid). None if no quote."""
    ya = xl._num((c.get("yes_ask") or {}).get("close_dollars")) * 100.0
    yb = xl._num((c.get("yes_bid") or {}).get("close_dollars")) * 100.0
    if result == "yes":
        return 100.0 - ya if ya > 0 else None
    if result == "no":
        return yb if yb > 0 else None
    return None


def _cvol(c: dict) -> float:
    """Candle volume, tolerant of the `_fp` spelling Kalshi's newer payloads use."""
    return xl._num(c.get("volume_fp")) or xl._num(c.get("volume")) or 0.0


def settle_value(m: dict):
    """The market's recorded settlement reference, whichever key the payload carries."""
    for k in ("settlement_value", "expiration_value", "settlement_value_dollars",
              "expiration_value_dollars"):
        v = m.get(k)
        if v not in (None, ""):
            return xl._num(v)
    return None


def frozen_reference_test(rows: list[tuple[float, float, float]]) -> dict:
    """rows = [(decided_from_ts, close_ts, settle_value)] for inside/tail windows.

    If the reference feed were truly halted, every window decided inside the SAME halt
    stretch would settle on the SAME last pre-halt print. Group by halt stretch (windows
    whose decided_from instants are within 49 h of each other and contiguous) and count
    distinct settlement values per stretch. Any stretch with > 1 distinct value means the
    feed printed during the "halt" — the premise, not the tape, is what fails."""
    rows = sorted(r for r in rows if r[2] is not None)
    stretches: list[list[tuple[float, float, float]]] = []
    for r in rows:
        if stretches and r[0] - stretches[-1][-1][1] <= 3600:
            stretches[-1].append(r)
        else:
            stretches.append([r])
    moved = [s for s in stretches if len(s) >= 2 and len({round(x[2], 4) for x in s}) > 1]
    return {"stretches": len(stretches), "multi": sum(1 for s in stretches if len(s) >= 2),
            "moved": len(moved), "windows": len(rows)}


def tape_read(series: str, ticker: str, decided_from: float, close_ts: float,
              result: str) -> dict:
    cs = candles(series, ticker, int(decided_from) - 60, int(close_ts) + 60)
    active = [c for c in cs if _cvol(c) > 0
              and xl._num(c.get("end_period_ts")) > decided_from]
    discs = [d for d in (discount_cents(c, result) for c in active) if d is not None]
    at_bar = sum(1 for d in discs if d >= DISCOUNT_BAR_CENTS)
    return {"candles": len(cs), "active": len(active), "quoted": len(discs),
            "mean_disc": (sum(discs) / len(discs)) if discs else None, "at_bar": at_bar}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="METALHALT recon census (read-only)")
    ap.add_argument("--max-event-pages", type=int, default=80)
    ap.add_argument("--tape-samples", type=int, default=12,
                    help="settled inside/tail markets to probe for a post-pin tape")
    args = ap.parse_args(argv)

    tz, real_tz = _et()
    print("METALHALT recon census — Kalshi metals markets vs the Pyth XAU/XAG halt calendar "
          "(read-only)")
    tz_note = ("America/New_York (zoneinfo)" if real_tz
               else "FIXED EDT FALLBACK — tzdata missing; DST boundaries may misclassify")
    print(f"timezone: {tz_note}")

    settled_evs = events("settled", args.max_event_pages)
    open_evs = events("open", args.max_event_pages)
    print(f"scanned events: settled={len(settled_evs)} open={len(open_evs)}")

    tally: dict[tuple[str, str], dict] = defaultdict(lambda: {"n": 0, "vol": 0.0})
    open_tally: dict[tuple[str, str], int] = defaultdict(int)
    samples: list[tuple[str, str, float, float, str, float]] = []
    frozen_rows: list[tuple[float, float, float]] = []
    metal_series: dict[str, int] = defaultdict(int)

    for ev in settled_evs:
        for m in ev.get("markets") or []:
            series = m.get("series_ticker") or ev.get("series_ticker") or ""
            if not METAL_SERIES.search(series):
                continue
            metal_series[series] += 1
            cls, decided_from = classify_window(_ts(m.get("open_time")), _ts(m.get("close_time")), tz)
            rec = tally[(series, cls)]
            rec["n"] += 1
            rec["vol"] += _vol(m)
            result = (m.get("result") or "").lower()
            if cls in ("inside", "tail"):
                frozen_rows.append((decided_from, _ts(m.get("close_time")), settle_value(m)))
            if cls in ("inside", "tail") and result in ("yes", "no") and _vol(m) > 0:
                samples.append((series, m.get("ticker") or "", decided_from,
                                _ts(m.get("close_time")), result, _vol(m)))
    for ev in open_evs:
        for m in ev.get("markets") or []:
            series = m.get("series_ticker") or ev.get("series_ticker") or ""
            if not METAL_SERIES.search(series):
                continue
            cls, _ = classify_window(_ts(m.get("open_time")), _ts(m.get("close_time")), tz)
            open_tally[(series, cls)] += 1

    if not metal_series:
        print("\nVERDICT: HOLD (UNTESTABLE) — no settled markets matched the metal series "
              "prefixes. Refresh METAL_SERIES against live tickers before reading anything "
              "into this. Not a kill.")
        return 0

    print("\n== C1/C2 structure + capacity (settled, by series × halt class) ==")
    print(f"  {'series':16s} {'class':9s} {'n':>6s} {'vol':>12s}   open-now")
    for (series, cls), rec in sorted(tally.items(), key=lambda kv: (kv[0][0], -kv[1]["n"])):
        print(f"  {series:16s} {cls:9s} {rec['n']:6d} {rec['vol']:12.0f}   "
              f"{open_tally.get((series, cls), 0)}")
    pinned = sum(r["n"] for (s, c), r in tally.items() if c in ("inside", "tail"))
    pinned_vol = sum(r["vol"] for (s, c), r in tally.items() if c in ("inside", "tail"))
    boundary = sum(r["n"] for (s, c), r in tally.items() if c == "boundary")
    print(f"\n  settled pinned (inside+tail): n={pinned} vol={pinned_vol:.0f} | boundary "
          f"(excluded) n={boundary} | with-volume samples={len(samples)} | floor={N_FLOOR}")

    print("\n== C0 frozen-reference test (do windows decided in the SAME halt settle on the "
          "SAME value?) ==")
    fr = frozen_reference_test(frozen_rows)
    print(f"  pinned windows with a recorded settlement value: {fr['windows']} | halt stretches: "
          f"{fr['stretches']} (with >= 2 windows: {fr['multi']}) | stretches whose settlement "
          f"value MOVED: {fr['moved']}")
    if fr["windows"] == 0:
        print("  (no settlement value on the market rows — the test cannot run; fall back to C3)")
    feed_moves = fr["multi"] > 0 and fr["moved"] > 0

    print("\n== C3 post-pin tape (1-min candles from decided_from → close; quote = candle close) ==")
    samples.sort(key=lambda s: -s[5])
    probed = with_tape = 0
    for series, ticker, decided_from, close_ts, result, _v in samples[:args.tape_samples]:
        r = tape_read(series, ticker, decided_from, close_ts, result)
        probed += 1
        if r["active"] and r["at_bar"]:
            with_tape += 1
        md = f"{r['mean_disc']:.1f}c" if r["mean_disc"] is not None else "n/a"
        mins = int((close_ts - decided_from) / 60)
        print(f"  {ticker:32s} {result:3s} decided {mins:4d}m early  candles {r['candles']:3d} "
              f"active {r['active']:3d}  mean-disc {md:>6s}  >= {DISCOUNT_BAR_CENTS:.0f}c: "
              f"{r['at_bar']}")
        time.sleep(0.05)
    if not probed:
        print("  (no settled inside/tail markets with volume and a result to probe)")

    print("\n== census verdict (pre-stage; the full FREEZE probe follows only if this promotes) ==")
    print(f"  pinned universe exists (n >= {N_FLOOR} settled w/ volume): "
          f"{len(samples) >= N_FLOOR} ({len(samples)})")
    print(f"  post-pin tape at a discount observed: {with_tape}/{probed} probed")
    print(f"  settlement reference moves inside the halt (C0): {feed_moves}")
    if feed_moves:
        print("  VERDICT: KILL (PREMISE) — windows decided inside the same nominal Pyth halt settle "
              "on DIFFERENT values, so the settlement feed Kalshi uses keeps printing (Pyth's "
              "24/7 metals indices, launched 2026-06). There is no frozen window to pin; the "
              "July exclusion of metals was right for this venue. Closes the metals axis of "
              "WS-005 D1.")
    elif len(samples) >= N_FLOOR and with_tape:
        print("  VERDICT: PROMOTE-TO-PROBE — point scripts/kalshi_freeze_study.py at the metals "
              "series with the Pyth halt calendar as the dark-window rule (WS-005 D1: a "
              "qualifying universe exists on the settlement-source axis).")
    elif not samples:
        print("  VERDICT: HOLD (UNIVERSE ABSENT) — Kalshi lists no metals windows inside the "
              "Pyth halt today. Re-run when the filed 24/7 metals schedule goes live (weekend "
              "windows would be 48 h of halt). Not a kill.")
    else:
        print("  VERDICT: HOLD (TOO NEW / NO TAPE) — pinned windows exist but either fewer than "
              f"{N_FLOOR} have settled with volume or nobody trades them after the halt starts. "
              "Re-run in ~2 weeks. Not a kill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
