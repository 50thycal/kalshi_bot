# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-05 11:14 AM CDT (run #20)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 503 | +$7.74 | +1.5 | 27 | baseline |
| **mmsell1** (5-20¢) | 28 | +$1.25 | **+4.5** | 21 | still ahead of control (3 runs) |
| **mmsell2** (10-20¢) | 18 | +$1.81 | **+10.1** | 11 | still well ahead (n growing) |
| **tfav** (NEW) | 0 | — | — | 1 | **NEW book just started — crypto favorite-buy** |
| theta (control) | 124 | −$14.19 | — | 1 | persistently negative |
| **theta3** (wide, ×1.25) | 55 | **−$10.14** | — | 1 | bounce reversed; **55/60 — verdict imminent** |
| theta1 / theta2 | 7 / 2 | −$4.29 / −$3.79 | — | 0/0 | near-idle |
| weather con | 242 | +$7.25 | +3.0 | 5 | trading normally (5 open) |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned; fully wound down |

**NEW book — `tfav`.** The TFAV crypto favorite-buy thesis has been promoted from a probe to a
paper book (fable session) and is now trading (1 open, first entry 11:05 AM CDT). Surfaced
automatically by the phase-3 all-books-individual query — exactly what that change was for. No
settles yet; nothing to judge.

**theta — heading to a NEGATIVE gate verdict.** theta3 at **55/60** is −$10.14 (its run-#18
bounce to −$0.18 fully reversed); the control (124) is −$14.19 and worsening; theta1/theta2 are
near-idle at n=7/2. Barring a sharp reversal in the last ~5 theta3 trades, the pre-registered
rule (keep only positive AND calibrated) points to **shelving the whole theta family** at the
gate. Decision point is ~1 run away.

**mmsell A/B — variants still ahead, 3 runs running.** mmsell1 +4.5¢, mmsell2 +10.1¢ vs
control +1.5¢/trade (n 28 / 18). mmsell2's edge is regressing toward realism (14.8→10.1) but
stays well above the control. Consistent enough to keep leaning positive; hold to ~150.

**Data (last-24h / latest CDT):** crypto_spot 2,874 (11:11 AM ✓), ladder 62,400 (11:11 AM ✓,
100% model-priced), forecasts/obs/ensembles/buckets ✓. **xgame 0/0** — matcher still broken.

**Research probes (on-demand):** WCPROP · XGAME (TFAV is now a live book, above).

**Headline:** the query caught a genuinely new book (`tfav`) the moment it started. mmsell
variants keep beating the control; weather con-only is stable; theta is one run from a likely
negative gate verdict. XGAME matcher remains the one broken piece.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · DECISION IMMINENT] theta3 55/60, −$10.14 — prep to shelve the family at the gate.**
   All four theta books are negative approaching n=60 (control −$14.19). Unless the last ~5
   theta3 trades sharply reverse, the pre-registered rule kills them. A fable session should be
   ready to disable the theta books (keep the collectors — the ladder/spot data is reusable) and
   write the post-mortem. Do NOT pre-empt the gate; just be ready.

2. **[mmsell A/B · early-positive, 3 runs] Variants ahead of control.** +4.5 / +10.1¢ vs +1.5¢.
   Hold to ~150 settled each; if it holds, promote the narrowed band and retire the control's
   wide 5-40¢.

3. **[tfav · NEW — just watch] First crypto favorite-buy book live.** No settles yet. Let it
   accumulate; its thesis (65-90¢ favorites) is the parked side of the theta work.

4. **[XGAME · real bug] Matcher makes 0 pairs (kal=14 / pm=169).** Unchanged. Fable fix.

5. **[weather · resolved] con-only stable** (+$7.25, 5 open); pruned books 0 open. Watch con
   stays net-positive as its own n grows.

*(Added: #1 theta decision-imminent (gate ~1 run out), #3 tfav new book. mmsell/xgame/weather
carried.)*
