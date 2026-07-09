# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-09 11:13 AM CDT (run #29)

**✅ RESOLVED — crypto_ladder collector recovered after the operator's worker restart.**
Confirmed at 7:54 AM CDT (52,426 snapshots/24h, latest within ~1 min of "now" at report time)
and still healthy now. theta resumed trading normally. Anomaly #0 from run #28 is closed.

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 1,460 | +$3.36 | +0.2 | 32 | −$1.49 window (small n=6); still breakeven |
| mmsell1 (5-20¢) | 752 | +$3.40 | +0.5 | 26 | −$1.43 window (n=4); breakeven |
| mmsell2 (10-20¢) | 494 | +$1.90 | +0.4 | 16 | −$1.43 window (n=4); breakeven |
| tfav | 210 | −$7.54 | −3.6 | 0 | resumed (+19), still negative; further past kill gate |
| theta (control) | 542 | **−$0.07** | −0.01 | 2 | **gave back its ENTIRE calm-streak gain** (see below) |
| theta1 | 196 | +$11.67 | +6.0 | 0 | flat-ish (−$0.92/16); still the one + theta book |
| theta2 | 96 | −$12.57 | −13.1 | 0 | **−$3.85 window (−38.5¢/trade)**, worst theta book now |
| theta3 | 134 | −$11.62 | −8.7 | 0 | flat window |
| wcprop | 0 | — | — | 0 | still zero trades ever |
| weather con | 294 | **−$1.24** | **−0.4** | 6 | **FIRST-EVER NET-NEGATIVE cumulative** (−$2.70 this batch) |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — two confirmations of standing verdicts, one new escalation.** theta's control has
now given back **its entire calm-streak gain across two windows**: peak was +$15.53 at run #27
(n=495); it's now **−$0.07 at n=542** — a **$15.60 giveback** in ~47 trades. This is exactly
what the run #27 calibration read (realized tails 1.4–2.6× modeled) predicted, playing out in
real time — about as clean a confirmation as this loop will ever see. Separately,
**weather_con posted its first-ever net-negative cumulative** (+$1.46 → **−$1.24** on the
~9 AM CDT settlement batch) — the "diagnose the drawdown" flag from run #27 was not
precautionary, the pattern continued and just crossed zero.

**theta — shelve verdict now has a live confirmation, not just a calibration argument.**
control round-tripped its entire positive P&L back to roughly breakeven; theta2 also worsened
sharply (−38.5¢/trade this window, now the worst theta book at −13.1¢ lifetime). theta1
remains the only book with a durable positive per-trade (+6.0¢, n=196) — still the sole
keep-candidate, still only under a fresh pre-registration.

**weather_con — escalating from "diagnose before scaling" to "diagnose now, the trend is
real."** Three bad days out of the last six (run #27) have now dragged cumulative P&L
negative for the first time in this book's history. This is no longer a caution flag on an
otherwise-green book — it needs the city/window/regime breakdown before any further capital or
attention, and arguably before letting it keep running unexamined.

**mmsell / tfav / wcprop / XGAME — unchanged in substance.** mmsell still breakeven noise;
tfav resumed trading and stayed negative, now further past its kill gate (n=210); wcprop still
0 trades ever; XGAME collector fresh, 0 new matches in 24h (tournament thinning to
semifinal/final) but tapes +10,254/24h — still runnable and still the top pending info action.

**Data (last-24h / latest CDT):** crypto_spot 2,876 (11:12 AM ✓), **ladder 52,426 (11:12 AM ✓,
RECOVERED)**, weather forecasts/obs/ensembles/buckets all fresh (11:06–11:13 AM ✓).
xgame_matches 19 total (0 new in 24h), xgame_tapes 10,254 (11:12 AM ✓). All green.

**Research probes (on-demand):** WCPROP = `xmarket_wc` (offline) + live `wcprop` book (0
trades). XGAME `xgame_tape_study` (runnable). Not run from the loop.

**Headline:** the ladder-collector restart held (resolved); theta's control fully round-tripped
its calm-streak gain back to breakeven, live-confirming the shelve calibration read; weather_con
crossed into net-negative territory for the first time, escalating the diagnose-the-drawdown
flag. mmsell/tfav/wcprop unchanged. Collectors all green.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · shelve — now LIVE-CONFIRMED, not just calibration] control gave back its entire
   +$15.53 peak (run #27) and sits at −$0.07 (n=542); theta2 worsened to −13.1¢/trade
   (n=96).** Two windows of giveback after one calm streak is exactly the miscalibrated-tail
   pattern (1.4–2.6× underpriced, per run #27's drill-down) predicted. Shelve the family; keep
   crypto_spot + ladder collectors in collect-only mode (now confirmed healthy post-restart).
   theta1 (+6.0¢, n=196) remains the only keep-candidate, only under a NEW pre-registered
   tail-calibration test at n≥350. Post-mortem in RESEARCH_JOURNAL.

2. **[weather_con · ESCALATED — cumulative now net-negative, first time ever] +$1.46 → −$1.24
   this run (−$2.70 on the ~9 AM CDT batch).** The 3-bad-days-of-6 pattern flagged in run #27
   was not a fluke; it has now erased the book's entire historical edge. Recommend a fable pass
   this week (not just "before scaling") on whether losing days share a cause (city / window /
   forecast-regime shift). This was the portfolio's only steady earner — its reversal is the
   most consequential open question right now.

3. **[tfav · kill rec active, more data] n=210 (further past the n≥150 gate), −3.6¢/trade,
   resumed trading and stayed negative.** Fails the pre-registered gate (2026-07-06 fable
   memo). `TFAV_ENABLED=false`.

4. **[mmsell · breakeven noise, unchanged] All three books ~flat cumulative (pooled
   ~+0.3¢/trade, n≈2,706), small negative windows this run (n too small to read).** Do NOT
   promote; keep as a zero-attention data book or prune for simplicity.

5. **[wcprop · kill rec stands] Zero trades ever.** `WCPROP_ENABLED=false` + optional one-shot
   `xmarket_wc` epitaph. Tournament ending soon (0 new matches in 24h — semifinal/final stage).

6. **[XGAME · run the tape study — top info action] 19 matched games, multiple completed and
   on tape, tournament thinning.** Run `xgame_tape_study` (fable/operator) before the dataset
   goes fully cold at tournament end; gate `xgame_book_enabled` on the result.

*(Changed this run: ladder-collector anomaly (#0 from run #28) marked RESOLVED — restart
confirmed effective. theta (#1) upgraded from "calibration argument" to "live-confirmed" via
the full giveback. weather_con (#2, was #4) ESCALATED — crossed into net-negative for the
first time; moved up the list given it's now the most consequential open question. tfav (#3),
mmsell (#4), wcprop (#5), XGAME (#6) unchanged in substance, renumbered.)*
