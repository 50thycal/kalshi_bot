# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-06 03:13 AM CDT (run #24)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 773 | −$9.16 | −1.2 | 33 | +$2.0 overnight bounce (10 trades) — still net-red |
| mmsell1 (5-20¢) | 234 | −$4.42 | −1.9 | 26 | +$1.0 (8 trades); net-red |
| mmsell2 (10-20¢) | 155 | −$4.27 | −2.8 | 15 | +$0.8 (5 trades); net-red |
| tfav | 26 | −$5.68 | −21.8 | 0 | +$1.7 on 1 trade (noise); net-red |
| theta (control) | 177 | −$41.97 | −23.7 | 1 | +$1.0 bounce, still catastrophic |
| theta3 | 64 | −$13.28 | −20.8 | 0 | at gate, worsening |
| theta1 / theta2 | 20 / 6 | −$5.92 / −$6.46 | — | 0/0 | dormant (no new trades ~7.5h); both red |
| wcprop | 0 | — | — | 0 | enabled LIVE book, armed, 0 trades |
| weather con | 242 | +$7.25 | +3.0 | 9 | only positive book; quiet overnight (see #6) |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — quiet overnight, no structural change.** Every active crypto/sports book ticked
slightly less-negative on tiny marginal n (mmsell +$2.0/10, theta control +$1.0/11, tfav
+$1.7/1) — this is small-n mean reversion on negative-skew books, **not** recovery: cumulatively
theta is −$42, all three mmsell books red, tfav −$5.68. **weather_con (+$7.25) remains the only
positive book** (unchanged — quiet overnight). The run #23 verdicts all stand.

**theta — shelve stands (unanimous).** control −$41.97 (n=177), theta3 −$13.28 (n=64, gate),
theta1/theta2 red and now dormant. No book meets the positive-AND-calibrated gate. The +$1
overnight tick on a −$42 book is noise.

**mmsell — whole family still net-negative.** control −1.2¢ (n=773), mmsell1 −1.9¢, mmsell2
−2.8¢. The overnight bounce (small n) doesn't change that no band is positive; prune candidate.

**wcprop — still 0 trades (armed).** WC games played earlier (tapes hit 163k) and have since
gone quiet overnight (only +5k to 168k). Still no entry — the armed-but-idle pattern persists;
continuing evidence the winner ladder may be efficiently priced (probe P1 kill) as matches settle.

**XGAME — taping steady (167,887/24h; +5k overnight as games ended).** Matcher steady at 13.
`xgame_tape_study` runnable once a matched game finishes with tape coverage.

**Data (last-24h / latest CDT):** crypto_spot 2,875 (03:13 AM ✓, 2 products), ladder 59,682
(03:13 AM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (03:08–
03:13 AM ✓). xgame_matches 13 (last new 01:32 PM), xgame_tapes 167,887 (03:11 AM ✓). All green.

**Research probes (on-demand):** WCPROP = `xmarket_wc` (offline P1/P2/P3 backtest) + the live
`wcprop` book above (armed, 0 trades). XGAME `xgame_tape_study` (runnable once a matched game
plays). Not run from the loop.

**Headline:** quiet overnight — small mean-reversion bounces but zero structural change; theta
(−$42, unanimous) and the whole mmsell family stay net-red, weather_con (+$7.25) the only green.
wcprop still armed/idle at 0. All collectors fresh; XGAME taping 168k.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · VERDICT — shelve the family (fable), unanimous] Every theta book negative.**
   control −$41.97 (n=177, −23.7¢), theta3 −$13.28 (n=64, at gate), theta1 −$5.92, theta2
   −$6.46 (both dormant). No book meets the pre-registered positive-AND-calibrated gate.
   Recommended fable action: disable the theta books, **keep the crypto_spot + ladder
   collectors** (a future fatter-tail model rebuilds from that dataset), write the post-mortem
   in RESEARCH_JOURNAL. Paper only, but bleeding fast — worth doing on the next fable pass.

2. **[mmsell · whole family net-negative — prune candidate] No mmsell book is positive.**
   control −1.2¢ (n=773), mmsell1 −1.9¢ (n=234), mmsell2 −2.8¢ (n=155). The variant edge is
   gone and the wide control is negative too — the maker-sell cheap-longshot edge isn't there.
   Recommended fable action: treat mmsell (all bands) as a prune candidate alongside theta; if
   kept, data-only, not a live-promotion path. Non-urgent (paper).

3. **[wcprop · live book, armed, 0 trades] Enabled and running, not a dormant probe.** No
   collector/switch action. Report-only: (a) the loop's own skill doc still mislabels WCPROP as
   "on-demand probe, no book" — a skill-doc fix was offered to the operator (awaiting go-ahead,
   not done here); the snapshots now describe it correctly. (b) Watch whether wcprop stays at 0
   as WC matches settle — a persistent 0 is real evidence the winner ladder is efficiently
   priced (probe P1 kill), which would close the family.

4. **[XGAME · FIXED — study nearly runnable] Collector matching (13) + taping (168k/24h).**
   Once a matched WC game finishes with tape coverage, run `xgame_tape_study` to grade the
   lead-lag thesis (P1–P4 in docs/IDEA_MODEL_20260704.md). No collector action needed.

5. **[tfav · negative] n=26, −$5.68 (−21.8¢/trade).** The initial +$0.99 (n=1) was noise; at
   n=26 it is net-negative (one +$1.7 trade this window is also noise). Let it reach a larger n
   before a final verdict, but it is trending toward "no edge." Do not act yet.

6. **[weather · watch — quiet overnight] con-only book +$7.25, 9 open — the only positive book.**
   No new entry since 4:59 PM CDT (~10h), but weather data collectors are all fresh at 3:1x AM,
   so the worker is alive — this is normal overnight selectivity, and the 9 open positions
   should settle at the ~9 AM CDT batch. Confirm entries resume after that batch; flag only if
   it stays frozen into the daytime.

*(Changed this run: no verdict changes — small overnight mean-reversion bounces across theta/
mmsell/tfav are small-n noise, not recovery (called out explicitly). #6 weather escalated
"resolved" → "watch" (no new entry ~10h overnight; confirm it resumes post-9AM-batch). theta
(#1), mmsell (#2), wcprop (#3), XGAME (#4), tfav (#5) unchanged in substance.)*
