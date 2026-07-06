# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-06 07:13 AM CDT (run #25)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 782 | −$8.35 | −1.1 | 27 | ~flat window (+$0.8/9); net-red |
| mmsell1 (5-20¢) | 241 | −$4.42 | −1.8 | 21 | flat (P&L unchanged); net-red |
| mmsell2 (10-20¢) | 161 | −$4.30 | −2.7 | 11 | flat; net-red |
| tfav | 28 | **−$11.31** | −40.4 | 4 | **deteriorated −$5.6 on 2 trades** (tail hit); worst/trade after theta |
| theta (control) | 189 | −$31.91 | −16.9 | 7 | **+$10 bounce window** (12 trades) but still −$32 cum |
| theta3 | 65 | −$11.47 | −17.6 | 1 | at gate, negative |
| theta1 / theta2 | 20 / 6 | −$5.92 / −$6.46 | — | 0/0 | dormant (unchanged); both red |
| wcprop | 0 | — | — | 0 | enabled LIVE book, armed, 0 trades |
| weather con | 251 | **+$7.82** | +3.1 | 3 | **entries RESUMED** (6:59 AM); +9 settled; only green |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — two real moves, neither changes a verdict.** theta's control had its best window
yet (**+$10 over 12 trades**) but is still −$31.91 cumulative (−16.9¢) — one good window on a
negative-skew book is not recovery (same discipline applied to last run's bounces). tfav went
the other way, **−$5.6 on 2 trades to −$11.31** (−40¢/trade) — the tail landed, as warned.
mmsell flat and still all-red. **weather_con resumed entering** (my run #24 watch item —
resolved: latest entry 6:59 AM, +9 settled to +$7.82) and stays the only positive book.

**theta — shelve stands despite the bounce.** control −$31.91 (n=189), theta3 −$11.47 (n=65,
gate), theta1/theta2 dormant-red. The +$10 window is exactly the kind of single-window noise
the loop is disciplined against; cumulatively no book is anywhere near the positive-AND-
calibrated gate. theta is trading actively again (7 open) so more settles are coming.

**tfav — now clearly negative.** −$11.31 at n=28 (−40¢/trade), worst per-trade after theta.
The −$5.6 two-trade swing is the favorite-buy negative skew (favorite loses → full-contract
loss). Trending firmly "no edge."

**wcprop — still 0 trades (armed).** Matcher added 2 games (13→15) and games are settling, yet
no wcprop entry — the armed-but-idle pattern holds; accumulating evidence the winner ladder is
efficiently priced (probe P1 kill).

**XGAME — matcher added 2 (13→15), taping 173k.** New WC games entered the window and matched
at 7:10 AM. `xgame_tape_study` runnable once a matched game finishes with tape coverage.

**Data (last-24h / latest CDT):** crypto_spot 2,869 (07:10 AM ✓, 2 products), ladder 59,922
(07:10 AM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (07:03–
07:13 AM ✓). xgame_matches 15 (last new 07:10 AM), xgame_tapes 173,121 (07:13 AM ✓). All green.

**Research probes (on-demand):** WCPROP = `xmarket_wc` (offline P1/P2/P3 backtest) + the live
`wcprop` book above (armed, 0 trades). XGAME `xgame_tape_study` (runnable once a matched game
plays). Not run from the loop.

**Headline:** theta control's +$10 window is single-window noise (still −$32 cum, shelve stands);
tfav's tail landed (−$11.31, clearly negative); mmsell flat/red; weather_con resumed and stays
the lone green book (+$7.82). Matcher added 2 games (15); wcprop still armed/idle. Collectors fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · VERDICT — shelve the family (fable), unanimous] Every theta book negative.**
   control −$31.91 (n=189, −16.9¢), theta3 −$11.47 (n=65, at gate), theta1 −$5.92, theta2
   −$6.46 (both dormant). The +$10 control window this run is single-window noise on a
   negative-skew book — cumulatively no book meets the positive-AND-calibrated gate.
   Recommended fable action: disable the theta books, **keep the crypto_spot + ladder
   collectors** (a future fatter-tail model rebuilds from that dataset), write the post-mortem
   in RESEARCH_JOURNAL. Paper only, but decided — worth doing on the next fable pass.

2. **[mmsell · whole family net-negative — prune candidate] No mmsell book is positive.**
   control −1.1¢ (n=782), mmsell1 −1.8¢ (n=241), mmsell2 −2.7¢ (n=161); flat this window. The
   variant edge is gone and the wide control is negative too. Recommended fable action: treat
   mmsell (all bands) as a prune candidate alongside theta; if kept, data-only. Non-urgent.

3. **[wcprop · live book, armed, 0 trades] Enabled and running, not a dormant probe.** No
   collector/switch action. Report-only: (a) the loop's own skill doc still mislabels WCPROP as
   "on-demand probe, no book" — a skill-doc fix was offered to the operator (awaiting go-ahead,
   not done here). (b) Matcher now at 15 games and matches are settling, yet wcprop is still at
   0 — a persistent 0 is real evidence the winner ladder is efficiently priced (probe P1 kill),
   which would close the family.

4. **[XGAME · FIXED — study nearly runnable] Matcher added 2 (13→15), taping 173k/24h.** Once a
   matched WC game finishes with tape coverage, run `xgame_tape_study` to grade the lead-lag
   thesis (P1–P4 in docs/IDEA_MODEL_20260704.md). No collector action needed.

5. **[tfav · clearly negative now] n=28, −$11.31 (−40¢/trade).** The −$5.6 two-trade swing is
   the favorite-buy negative skew. Worst per-trade book after theta. Trending firmly "no edge";
   a prune candidate if the next window confirms. Let it settle its 4 open first; do not act yet.

6. **[weather · resolved — resumed] con-only book +$7.82, 3 open — the only positive book.**
   Run #24's watch (no entries ~10h overnight) is resolved: it resumed entering at 6:59 AM CDT
   and added 9 settled trades positively. Healthy; no action.

*(Changed this run: theta (#1) control had a +$10 window but verdict UNCHANGED — flagged as
single-window noise (consistent with last run's discipline). #5 tfav escalated "negative" →
"clearly negative" (−$5.68→−$11.31, tail hit). #6 weather "watch" → "resolved/resumed" (entries
came back at 6:59 AM). XGAME (#4) matcher 13→15. mmsell (#2), wcprop (#3) unchanged in substance.)*
