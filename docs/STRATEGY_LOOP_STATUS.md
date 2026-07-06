# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-06 11:13 AM CDT (run #26)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 798 | −$7.81 | −1.0 | 35 | flat window (+$0.5/16); net-red |
| mmsell1 (5-20¢) | 254 | −$5.20 | −2.0 | 26 | slight down window (−$0.8/13); net-red |
| mmsell2 (10-20¢) | 169 | −$5.17 | −3.1 | 14 | slight down window (−$0.9/8); net-red |
| **tfav** | 50 | **+$3.99** | **+8.0** | 5 | **REVERSED — swung +$15.3 on 22 trades**, now net-positive |
| theta (control) | 228 | **−$46.24** | −20.3 | 7 | **−$14.3 this window** (39 trades); worst cumulative in system |
| theta3 | 79 | −$20.24 | −25.6 | 3 | **now well past gate (n=79)**, −$8.8 this window |
| theta1 | 52 | **−$29.45** | **−56.6** | 0 | resumed trading, **cratered** (was −$5.92 @ n=20) |
| theta2 | 21 | **−$23.46** | **−111.7** | 0 | resumed trading, **cratered** (was −$6.46 @ n=6); worst ¢/trade of any book |
| wcprop | 0 | — | — | 0 | enabled LIVE book, armed, 0 trades (unchanged) |
| weather con | 251 | +$7.82 | +3.0 | 8 | settled flat (batch already ran), 5 new opens; only green |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — a correlated tail event hit the entire theta family at once; tfav whipsawed hard
positive.** This is the first run under the new 8-hour cadence, and the wider window caught a
real event: **all four theta books took simultaneous marginal losses** this window (control
−36.7¢/trade, theta3 −62.6¢/trade, theta1 −73.5¢/trade, theta2 **−113.3¢/trade** — its worst
window yet). theta1/theta2 had gone quiet for ~1.5 days and **resumed trading hard this
window**, immediately eating the tail. Because all theta books share the same underlying
crypto markets (just different bands/multipliers), a single sharp move hits all of them
together — this is exactly the "model underprices tails" failure mode from the original
diagnosis, now showing up at scale. **This materially reinforces the shelve verdict**, it does
not change it. Meanwhile **tfav reversed hard**: −$11.31 (n=28) → **+$3.99 (n=50)**, a +$15.30
swing on 22 trades (+69.5¢/trade this window) — the same small-n whipsaw discipline applies:
neither the prior negative nor this positive read is decisive.

**theta — verdict now stronger, not just standing.** theta3 is decisively past its n≥60 gate
(now n=79) and got *more* negative as n grew (−$11.47→−$20.24). theta1 (n=52, −$29.45) and
theta2 (n=21, −$23.46) both cratered on resumed trading. No book is within reach of the
positive-AND-calibrated gate; the control's cumulative loss is now −$46.24, the largest in the
whole portfolio.

**tfav — do not overreact either direction.** Two consecutive windows have each swung this book
by more than its entire cumulative P&L (−$5.6 last run, +$15.3 this run). At n=50 it's still
too small and too high-variance to call. Recommend treating it like theta/mmsell — needs a much
larger n (proposing ~100+) before any promote or prune read.

**mmsell — unchanged, still flat-to-red.** All three bands moved <$1 this window and remain
cumulatively negative; the prune-candidate read stands.

**wcprop — still 0 trades (unverified this run, last confirmed armed via logs at run #21).**
No new evidence either way this window; carrying forward unchanged.

**XGAME — steady.** Matcher unchanged at 15 games (no new matches since 7:10 AM); tapes
+11,692 in ~4h (173,121→184,813) — collection pace holding.

**Data (last-24h / latest CDT):** crypto_spot 2,875 (11:13 AM ✓, 2 products), ladder 61,122
(11:13 AM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (11:04–
11:13 AM ✓). xgame_matches 15 (last new 07:10 AM), xgame_tapes 184,813 (11:12 AM ✓). All green.

**Research probes (on-demand):** WCPROP = `xmarket_wc` (offline P1/P2/P3 backtest) + the live
`wcprop` book above (armed, 0 trades). XGAME `xgame_tape_study` (runnable once a matched game
plays). Not run from the loop.

**Headline:** a shared tail event hit all four theta books this window (control now −$46.24,
worst in the portfolio; theta1/theta2 cratered on resumed trading) — reinforces shelve. tfav
whipsawed −$11.31→+$3.99 on n=50 — too volatile/small to read either way. mmsell flat-red.
weather_con (+$7.82) the only steady green book. Collectors fresh; XGAME steady at 15/185k.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · VERDICT — shelve the family (fable), now stronger] Every theta book negative and
   the gate-passing book got worse with more data.** control −$46.24 (n=228, −20.3¢, largest
   loss in the portfolio), theta3 −$20.24 (n=79, **now well past the n≥60 gate** and more
   negative than at the gate), theta1 −$29.45 (n=52, cratered on resumed trading), theta2
   −$23.46 (n=21, **−111.7¢/trade, worst in the system**). This window's correlated hit across
   all four books is itself evidence for the original diagnosis (model underprices tails).
   Recommended fable action: disable the theta books, **keep the crypto_spot + ladder
   collectors** (a future fatter-tail model rebuilds from that dataset), write the post-mortem
   in RESEARCH_JOURNAL. Paper only, but decisively confirmed — good candidate for the next
   fable pass.

2. **[mmsell · whole family net-negative — prune candidate] No mmsell book is positive.**
   control −1.0¢ (n=798), mmsell1 −2.0¢ (n=254), mmsell2 −3.1¢ (n=169); flat this window.
   Recommended fable action: treat mmsell (all bands) as a prune candidate alongside theta; if
   kept, data-only. Non-urgent.

3. **[wcprop · live book, armed, 0 trades] Enabled and running (last verified via worker logs
   at run #21), not a dormant probe.** No collector/switch action. Report-only: (a) the loop's
   skill-doc mislabel fix is still offered/awaiting your go-ahead. (b) Still 0 trades — continuing
   (if weaker, since not re-verified this run) evidence the winner ladder may be efficiently
   priced (probe P1 kill).

4. **[XGAME · FIXED — study nearly runnable] Matcher steady at 15, taping steady (185k/24h,
   +11.7k this window).** Once a matched WC game finishes with tape coverage, run
   `xgame_tape_study` to grade the lead-lag thesis (P1–P4 in docs/IDEA_MODEL_20260704.md). No
   collector action needed.

5. **[tfav · HIGH VARIANCE — do not read either direction] Reversed hard: −$11.31 (n=28) →
   +$3.99 (n=50), a +$15.30 swing in one window.** Two straight windows have each moved P&L by
   more than the book's entire cumulative total — this is a small-n, high-variance book, not a
   trend in either direction. Recommend waiting for a much larger n (~100+) before any
   promote/prune read; the loop will keep tracking but will not call a verdict prematurely.

6. **[weather · resolved] con-only book +$7.82 (n=251, unchanged — no new settlement since the
   last batch), 8 open (5 new entries this window) — the only steady positive book.** Healthy,
   actively trading; no action.

*(Changed this run: theta (#1) escalated from "shelve" to "shelve — now stronger," with the
correlated-tail-event framing and theta3 confirmed well past gate. #5 tfav reworded from
"clearly negative" to "HIGH VARIANCE — do not read either direction" after a hard reversal to
positive; raised the bar to n~100+ before any verdict. mmsell (#2), wcprop (#3), XGAME (#4),
weather (#6) unchanged in substance. This is the first run under the new 8-hourly cadence.)*
