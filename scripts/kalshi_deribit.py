"""Kalshi crypto binary vs Deribit options-implied digital — relative value against a deep,
sophisticated options market (NOT a forecast).

A Kalshi 'BTC >= $X at T' binary's fair value is the risk-neutral P(BTC >= X at T). That
probability is extractable model-free from the Deribit BTC option smile via
Breeden-Litzenberger: P(S_T >= K) = -dCall/dK. We reprice calls off the interpolated smile
(so skew is baked in) and finite-difference. Then we compare to Kalshi's live binary price
across BTC threshold strikes (KXBTCMAXY = reach>=X, KXBTCMINY = drop<=X), and report where
Kalshi diverges from the options-implied digital by more than the fee — the candidate edge:
buy the cheap side on Kalshi (Deribit is the oracle), hold to settlement, one-venue.

Read-only public APIs (Kalshi + Deribit, both no-auth), stdlib only. Reuses xvenue_crypto for
the Kalshi crypto market list. Usage:
    {"type":"script","name":"kalshi_deribit","args":["--max-gap-days","12"]}
"""

from __future__ import annotations

import argparse
import calendar
import math
import re
import time
from collections import defaultdict

import xvenue_crypto as xc  # fetch_kalshi_crypto
import xvenue_leadlag as xl  # _get (browser-UA fetch), _num

DERIBIT = "https://www.deribit.com/api/v2"
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_INSTR = re.compile(r"^BTC-(\d{1,2})([A-Z]{3})(\d{2})-(\d+)-([CP])$")
_FUT = re.compile(r"^BTC-(\d{1,2})([A-Z]{3})(\d{2})$")
_YEAR = 365.25 * 86400


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(f: float, k: float, t: float, sig: float) -> float:
    """Black-76 call (undiscounted, r~0 for crypto), priced off a forward F."""
    if t <= 0 or sig <= 0 or k <= 0 or f <= 0:
        return max(f - k, 0.0)
    srt = sig * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sig * sig * t) / srt
    return f * _N(d1) - k * _N(d1 - srt)


def _exp_unix(dd: str, mon: str, yy: str) -> int | None:
    m = _MON.get(mon)
    if not m:
        return None
    return calendar.timegm((2000 + int(yy), m, int(dd), 8, 0, 0))  # Deribit expires 08:00 UTC


def fetch_deribit():
    """Returns (smiles, forwards): smiles[exp_unix] = {strike: iv}, forwards[exp_unix] = F."""
    smiles: dict[int, dict[float, float]] = defaultdict(dict)
    under: dict[int, float] = {}
    data = xl._get(f"{DERIBIT}/public/get_book_summary_by_currency?currency=BTC&kind=option")
    for r in (data or {}).get("result") or []:
        m = _INSTR.match(r.get("instrument_name") or "")
        if not m or m.group(5) != "C":
            continue
        exp = _exp_unix(m.group(1), m.group(2), m.group(3))
        iv = r.get("mark_iv")
        if exp is None or iv is None:
            continue
        smiles[exp][float(m.group(4))] = float(iv) / 100.0
        if r.get("underlying_price"):
            under[exp] = float(r["underlying_price"])
    # prefer the per-expiry future as the forward F
    fwd: dict[int, float] = {}
    fdata = xl._get(f"{DERIBIT}/public/get_book_summary_by_currency?currency=BTC&kind=future")
    for r in (fdata or {}).get("result") or []:
        m = _FUT.match(r.get("instrument_name") or "")
        mp = r.get("mark_price")
        if m and mp:
            exp = _exp_unix(m.group(1), m.group(2), m.group(3))
            if exp:
                fwd[exp] = float(mp)
    forwards = {e: fwd.get(e, under.get(e)) for e in smiles}
    return smiles, forwards


def iv_interp(smile: dict[float, float], k: float) -> float | None:
    ks = sorted(smile)
    if not ks:
        return None
    if k <= ks[0]:
        return smile[ks[0]]
    if k >= ks[-1]:
        return smile[ks[-1]]
    for i in range(1, len(ks)):
        if ks[i] >= k:
            k0, k1 = ks[i - 1], ks[i]
            w = (k - k0) / (k1 - k0)
            return smile[k0] * (1 - w) + smile[k1] * w
    return None


def digital_ge(smile, f: float, t: float, k: float) -> float | None:
    """P(S_T >= K) = -dCall/dK, calls repriced off the interpolated smile (skew-aware)."""
    h = max(k * 0.0025, 25.0)

    def call(kk):
        iv = iv_interp(smile, kk)
        return bs_call(f, kk, t, iv) if iv else None

    cu, cd = call(k + h), call(k - h)
    if cu is None or cd is None:
        return None
    return min(1.0, max(0.0, -(cu - cd) / (2 * h)))


def fee_cents(p: float) -> float:
    return math.ceil(7.0 * p * (1 - p))   # taker fee per contract (cents)


def _kalshi_day_unix(day: str) -> int | None:
    try:
        return calendar.timegm(time.strptime(day, "%Y-%m-%d")) + 8 * 3600
    except (ValueError, TypeError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-gap-days", type=float, default=12.0,
                    help="max |Deribit expiry - Kalshi close| to compare")
    ap.add_argument("--min-vol", type=float, default=0.0, help="skip thin Kalshi markets")
    args = ap.parse_args(argv)
    now = int(time.time())

    smiles, forwards = fetch_deribit()
    exps = sorted(e for e in smiles if forwards.get(e) and e > now)
    print("=== Kalshi BTC binary vs Deribit options-implied digital ===")
    print(f"  Deribit BTC option expiries (future): "
          f"{[time.strftime('%Y-%m-%d', time.gmtime(e)) for e in exps]}")
    if not exps:
        print("  (no Deribit option data)")
        return 0

    kc = [k for k in xc.fetch_kalshi_crypto(400) if k["asset"] == "BTC" and k["vol"] >= args.min_vol]
    print(f"  Kalshi BTC threshold markets: {len(kc)}\n")

    rows = []
    for k in kc:
        kclose = _kalshi_day_unix(k["day"])
        if not kclose:
            continue
        exp = min(exps, key=lambda e: abs(e - kclose))
        gap = abs(exp - kclose) / 86400.0
        if gap > args.max_gap_days:
            continue
        t = (exp - now) / _YEAR
        f = forwards[exp]
        p_ge = digital_ge(smiles[exp], f, t, k["strike"])
        if p_ge is None:
            continue
        deribit = p_ge if k["dir"] == "up" else (1.0 - p_ge)
        div = (k["yes"] - deribit) * 100.0       # +: Kalshi richer than Deribit -> sell Kalshi
        rows.append((abs(div), div, k, deribit, gap, f))

    rows.sort(reverse=True)
    print(f"  {'asset':>4} {'dir':>4} {'strike':>9} {'kClose':>10} {'kYes':>5} {'derib':>6}"
          f" {'div':>6} {'fee':>4} {'gap_d':>5} {'edge':>6}  ticker")
    for _ad, div, k, deribit, gap, _f in rows[:40]:
        fee = fee_cents(k["yes"]) + fee_cents(deribit)   # rough round-trip-ish
        edge = abs(div) - fee
        print(f"  {k['asset']:>4} {k['dir']:>4} {k['strike']:>9,.0f} {k['day']:>10}"
              f" {k['yes'] * 100:4.0f}c {deribit * 100:5.1f}c {div:+5.1f}c {fee:4.0f}c"
              f" {gap:5.1f} {edge:+5.1f}c  {k['ticker']}")
    if rows:
        divs = [abs(d[1]) for d in rows]
        print(f"\n  {len(rows)} comparable markets | avg|div|={sum(divs) / len(divs):.1f}c"
              f"  median={sorted(divs)[len(divs) // 2]:.1f}c"
              f"  %>4c={100.0 * sum(1 for d in divs if d >= 4) / len(divs):.0f}%")
        print("  div = Kalshi yes - Deribit digital (+ = Kalshi too rich, buy NO / - = too cheap,"
              " buy YES). edge = |div| - fees. CAVEAT: expiry gap + index basis (BRTI vs Deribit).")
    else:
        print("  (no comparable Kalshi/Deribit expiry pairs within gap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
