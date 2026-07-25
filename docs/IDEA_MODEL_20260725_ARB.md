# Idea-model run 2026-07-25 — structural / arbitrage-adjacent scope

Scoped run triggered by the operator's question: *"can we buy both sides such that we win no
matter what?"* Phase 0 confirmed the locked-arb family has been scanned and ruled out; this run
re-opens **only the specific pockets the existing scan cannot see**, and then runs the full
anti-anchored slate so the answer isn't just "structure is dead."

---

## Phase 0 — grounding (what's already settled)

**Live-book baseline (the correlation lens).** Per `PORT` (2026-07-22): the portfolio holds **one**
independent realizable-+EV strategy — the **mmsell maker-sell family**, with `mmsell10` (+1.40¢
realizable, Sharpe 1.48) the single genuine book. Weather cells are net negative, `theta` shelved,
`PIN15` retired. Binding constraint = **edge supply, not allocation**.

**Base rate.** 16 idea-model promotions → 0 currently-live paper books. Structural family
**0-for-2** (DECAY, FEDRV). Obs-pin is the only family that has ever passed (1-for-7).
**Promote conservatively.**

**The locked-arb record specifically.** `scripts/kalshi_arb.py` scanned 882 events (Jul 4) and
1,021 events (Jul 25 re-run, this session): **zero real arbs** both times. Liquid MECE sets sit at
Σ(yes_ask) 0.97–0.99 — reliably 1–3¢ *short* of a lock. Both runs' only "hits" were strike-parsing
bugs (fixed; `tests/test_kalshi_arb.py`).

**The structural fact that kills the naive form.** On Kalshi YES and NO share **one** orderbook, so
`no_ask = 100 − yes_bid`. Both sides of a *single* market cost `yes_ask + (1 − yes_bid) =
100 + spread` ≥ $1 before fees. A one-market lock is **impossible by construction** — every real
Dutch book must be multi-leg.

**Where the existing scanner is blind (this run's opening).** `scan_event()` iterates
`event["markets"]` — it is **within-event only**. Three consequences, none previously tested:
1. **`KXMVE*` parlay series are explicitly skipped** (`series_ticker.startswith("KXMVE")` →
   `continue`, commented "parlay noise").
2. **Cross-series logical containment is never checked** — e.g. a "max ≥ K by T" market vs a
   "level ≥ K at T" market are separate events, so their hard ordering is unexamined.
3. **Candidate-winner sets without the `mutually_exclusive` flag fall through** — subtitles are
   person names, so `_parse_bucket` returns `None`, the MECE branch is skipped, the `ge`-ladder
   branch finds no legs, and the event returns `None` **silently**. Coverage is unmeasured.

## Phase 1 — live board (`kalshi_market_survey`, 2026-07-25)

7,793 events / 70,865 markets / 39,949 traded.

| category | series | volume | avg spread |
|---|---|---|---|
| Sports | 679 | 620.4M | 12.7¢ |
| Elections | 624 | 577.7M | **5.7¢** |
| Politics | 415 | 142.5M | 11.9¢ |
| Economics | 230 | 101.6M | **17.8¢** |
| Crypto | 75 | 87.6M | 6.5¢ |
| Sci/Tech | 102 | 55.6M | 15.2¢ |

The number that reframes the run: **a handful of high-volume series quote enormous spreads** —
`KXGOVCA` **92.0¢** (49.2M vol), `KXMAYORLA` **80.2¢** (87.2M), `KXPGATOUR` **55.2¢** (117.3M,
147 markets, weekly), `KXWTIMAX` 42.8¢, `KXBTCMAX150` 40.8¢. Volume is cumulative, so this is a
*flow census question*, not yet an edge — but it is the widest liquidity-provision pocket on the
board and the portfolio has no exposure to it.

---

## Phase 2 + 3 — slate and screen

16 candidates across all six mechanics. Axes: **Corr** (to live books), **Prior** (meta-lessons),
**Cost**, **Test** (testable NOW), **Cap** (capacity/cadence), **Reuse**.

| # | candidate | mechanic × category | Corr | Prior | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|---|
| S1 | **MVELOCK** — `KXMVE` parlay vs its component single markets; parlay ⊆ leg ⟹ `parlay_bid > leg_ask` is a **hard lock** | structural × cross-series | ++ | o | + | ++ | ? | ++ | **PROMOTE** (→ XLOCK P1) |
| S2 | **TOUCHTERM** — `P(max_{t≤T} ≥ K) ≥ P(S_T ≥ K)`; touch series vs terminal series, and nested date windows | structural × cross-series | ++ | o | o | ++ | o | ++ | **PROMOTE** (→ XLOCK P2) |
| S3 | **MECECOV** — census the multi-outcome events the within-event scanner silently skips | structural × meta | ++ | o | ++ | ++ | n/a | ++ | **PROMOTE** (→ XLOCK P3) |
| S4 | NEGRISK — buy-all-NO on very-wide (100s of legs) candidate sets where Σ(yes_bid) > 1 | structural × elections | ++ | − | −− | + | − | ++ | **KILL** — per-leg 1¢ fee × N swamps the credit, and these sets are provably non-exhaustive (no "none of the above" leg) — the exact Peru/Netflix trap already logged |
| S5 | XVENUELOCK — Kalshi YES + Polymarket NO on the same outcome | structural × cross-venue | + | −− | −− | + | o | ++ | **KILL** — measured divergence 1–1.6¢ vs 2–4¢ round-trip; plus PM KYC/gas. Already ruled out |
| M1 | **WIDEQUOTE** — rest quotes inside the 55–92¢ spreads on `KXGOVCA`/`KXMAYORLA`/`KXPGATOUR` | maker × elections/sports | o | + | ++ | o | ? | + | **PROMOTE (census-first)** |
| M2 | PGAMM — maker-sell 5–35¢ on golf outright ladders | maker × sports | −− | ++ | + | ++ | ++ | ++ | **HOLD** — the edge is real but it is the **mmsell driver** (sell overpriced longshots); PORT clusters it into the existing single book. Revisit as an mmsell *cell*, not a new book |
| M3 | ELECMM — maker-sell on election person-ladders | maker × elections | −− | + | + | + | −− | ++ | **HOLD** — same mmsell driver; lumpy one-off settles can't build a readable track record |
| M4 | NBAMM — maker on `KXNEXTTEAMNBA` (559 legs, 2.5¢ spread, 231M vol) | maker × sports | − | o | −− | + | ++ | + | **KILL** — 2.5¢ spread at 6¢ avg price leaves nothing after the 1¢ fee floor; already sharp |
| O1 | ECON-REACT re-run — more econ prints have settled since the 07-21 HOLD | obs-pin × economics | ++ | ++ | + | − | + | ++ | **HOLD (scheduled)** — only 4 days elapsed vs ~20 settles at HOLD; re-run in ~2 weeks, not now |
| O2 | HORMUZAIS — public AIS traffic vs `KXHORMUZNORM` (29.6¢ spread, 31M vol) | obs-pin × politics | ++ | + | o | −− | − | − | **KILL** — TRACKPIN parent: one-off settle, no recurring instrument, AIS feed not freely pollable at cadence |
| O3 | GASPIN — AAA daily gas print vs Kalshi gas markets | obs-pin × economics | ++ | + | o | −− | + | + | **KILL** — the ECON-REACT contamination audit already established these settle constantly but **never trade post-release**: no tape to pin |
| D1 | ECONDIR — directional taking on the widest liquid category (Economics, 17.8¢) | directional × economics | ++ | −− | −− | + | + | o | **KILL** — 17.8¢ spread crossed as a taker; cost dominates (lesson 5) |
| L1 | BOOKLAG — Kalshi vs sportsbook closing lines | lead-lag × sports | + | − | o | −− | ++ | − | **KILL** — no free sportsbook odds feed at cadence; XGAME's shared-feed symmetry finding transfers |
| E1 | FOMCREACT — post-FOMC repricing lag on `KXFEDDECISION` (13.8¢, 37.6M) | event-conditional × rates | ++ | o | o | − | −− | + | **KILL** — FEDRV closed rates as internally efficient; 8 settles/yr is uninvestable cadence |
| Q1 | QUEUE — queue-position / depth signal | microstructure × any | ++ | −− | − | −− | o | − | **KILL** — OFLOW closed the family (corr ≈ 0.008, −3.4¢ net); Kalshi exposes no queue position |

**Promoted: 2.** S1+S2+S3 merge into one probe (**XLOCK** — one scan, three pre-registered
predictions, since all three are the same "look outside the event boundary" cut). **WIDEQUOTE**
promotes census-first, on the STREAMPIN/SEASONPIN precedent.

---

## Why revive a 0-for-2 family at all

The skill's rule is that a ruled-out idea returns only with a **specific, material difference**
from what was tested. XLOCK's difference is not a new belief about efficiency — it is that the
existing scan's own code path **excludes** these pockets:

- `KXMVE` is a `continue` statement, not a null result.
- Cross-series containment was never in scope of a function that takes one event.
- The unflagged-candidate-set skip is silent, so "882 events scanned" and "1,021 events scanned"
  are both **denominators of unknown completeness**.

That is a coverage claim, and it is cheap to settle. The **prior remains LOW** — the tightness
distribution (0.97–0.99 on everything liquid) says Kalshi's arbitrageurs are competent, and the
most likely outcome is P3 returns a coverage number and P1/P2 return zero. That is still a win:
it converts "we scanned for arbs" into "we scanned for arbs *and here is the fraction of the board
that scan covers*", which is what makes the standing monitor trustworthy.

## What this run does NOT do

It does not promote another mmsell cell. PORT's finding is that the portfolio needs a **second
independent** edge, and PGAMM/ELECMM/NBAMM all share the maker-sell longshot driver. They are
logged as HOLDs so a future run doesn't regenerate them as if new.
