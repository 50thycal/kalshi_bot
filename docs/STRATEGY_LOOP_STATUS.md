# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-09 03:14 AM CDT (run #28)

**⚠ ANOMALY — the crypto_ladder collector looks STALLED (~3h).** Its latest snapshot is
**11:59 PM CDT (Jul 8)**, ~3h15m ago, vs a ~5-min cadence — STALE by the skill's own rule.
Meanwhile crypto_spot (3:14 AM), all weather collectors (3:00–3:14 AM), and mmsell trading
(last entry 1:57 AM) are all fresh — so the worker is alive; this is specific to the
ladder-snapshot / theta subsystem. theta stopped entering at **11:28 PM CDT** (0 open now),
right as the ladder snapshots stopped. Cause needs an operator/logs check (worker thread
died / Kalshi ladder endpoint / a silent restart that didn't revive that collector). The
queued logs probe returned late with **zero lines matching "ladder"**, on the **same Jul-6
deployment (no restart since 07-06 12:27 UTC)** — weakly corroborating a silent/stalled
collector on a still-running process (inconclusive: the collector may log under a different
string, or the lines scrolled past the 250-line window). **This matters because the ladder
dataset is exactly what the theta-shelve rec says to preserve** — if the collector is down,
"keep collecting" is moot until it's restarted. Watch run #29 for auto-recovery.

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 1,454 | +$4.85 | +0.3 | 27 | +$2.56 overnight; breakeven noise |
| mmsell1 (5-20¢) | 748 | +$4.83 | +0.6 | 23 | breakeven+ |
| mmsell2 (10-20¢) | 490 | +$3.33 | +0.7 | 14 | breakeven+ |
| tfav | 191 | −$7.08 | −3.7 | 0 | **dormant** (no new trades); gate crossed neg → kill rec |
| theta (control) | 502 | +$9.28 | +1.8 | 0 | **gave back −$6.25** this window (calm streak reversing, as predicted) |
| theta1 | 180 | +$12.59 | +7.0 | 0 | ~flat (+1 trade); the only theta with a + per-trade |
| theta2 / theta3 | 86 / 127 | −$8.72 / −$11.58 | −10.1 / −9.1 | 0/0 | dormant, unchanged; both negative |
| wcprop | 0 | — | — | 0 | still zero trades ever |
| weather con | 277 | +$1.46 | +0.5 | 17 | dormant since 5:01 PM (overnight); 17 open await ~9 AM batch |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — quiet overnight + one real anomaly (ladder collector stale).** Books barely
moved: mmsell nudged up (still breakeven), theta control **gave back −$6.25** of its calm-streak
gains — the exact "miscalibrated tail-seller reverts" behavior run #27's calibration read
predicted, now visible within 5 hours. tfav, theta2/3, and weather_con are all dormant (no new
settles). Every run-#27 verdict is unchanged. The one thing that needs eyes is the **stale
crypto_ladder collector** (above).

**theta — no change; the −$6.25 giveback reinforces the shelve read.** control still +$9.28
cumulative but shedding it as soon as a tail bites; calibration verdict (run #27: realized
tails 1.4–2.6× modeled) governs. theta1 (+7.0¢, n=180) remains the sole keep-candidate under a
NEW pre-registration, not a silent extension.

**tfav / mmsell / wcprop / weather_con — unchanged from run #27.** tfav kill rec active
(gate crossed negative, now dormant); mmsell breakeven noise; wcprop 0-trades kill rec stands;
weather_con scale-up still PAUSED pending the bad-days diagnosis (no new data overnight — its
17 open settle at the ~9 AM CDT batch, next run will show whether the drawdown continued).

**XGAME — collector fresh, semifinal lull.** 19 matched games (unchanged), tapes 7,056/24h
(last tape 2:01 AM — tapes only write during live matches, so the lull is expected, not a
stall). `xgame_tape_study` still runnable and still the top pending info action.

**Data (last-24h / latest CDT):** crypto_spot 2,878 (3:14 AM ✓, 2 products), **crypto_ladder
53,386 — STALE, last 11:59 PM CDT (see anomaly)**, weather forecasts/obs/ensembles/buckets all
fresh (3:00–3:14 AM ✓). xgame_matches 19 (last new Jul 8 5:32 AM), xgame_tapes 7,056 (2:01 AM,
lull). Ladder is the one red flag; everything else green.

**Research probes (on-demand):** WCPROP = `xmarket_wc` (offline) + live `wcprop` book (0 trades).
XGAME `xgame_tape_study` (runnable). Not run from the loop.

**Headline:** quiet overnight; theta control shed −$6.25 of its calm-streak gain (calibration
read confirmed); all run-#27 verdicts stand. **One anomaly: the crypto_ladder collector is ~3h
stale (theta stopped entering at 11:28 PM) while spot/weather/mmsell stayed fresh — needs an
operator check, and it's the dataset the theta-shelve rec relies on preserving.**

---

## Carried-over suggestions (review these; do not expect the loop to act)

0. **[NEW · ops health — verify the crypto_ladder collector] It is ~3h stale as of 3:14 AM CDT
   (last snapshot 11:59 PM), while spot + weather + mmsell stayed fresh, and theta stopped
   entering at 11:28 PM.** Likely a stalled ladder/theta worker thread or a Kalshi
   ladder-endpoint issue (no deploy landed in the gap, so not a code change). Recommended
   check: pull worker logs filtered for the ladder/crypto scan and confirm whether it's
   erroring or silently dead; a worker restart may be needed. **Priority because the ladder
   dataset is the asset the theta-shelve plan preserves** — a dead collector makes "keep
   collecting" moot. If it self-recovers by run #29, downgrade to noise.

1. **[theta · shelve per the rule] Calibration fails on every book (realized tails 1.4–2.6×
   modeled); the cumulative positive is one calm streak that is already reverting (control
   −$6.25 this run).** Shelve the family; keep the spot + ladder collectors in collect-only mode
   (and see #0 — verify the ladder collector is actually alive). If keeping anything, theta1
   only (+7.0¢, n=180) under a NEW pre-registered tail-calibration test at n≥350. Post-mortem
   in RESEARCH_JOURNAL.

2. **[tfav · kill rec active] n=191 ≥ 150 gate, −3.7¢/trade, now dormant.** Fails the
   pre-registered gate (2026-07-06 fable memo). `TFAV_ENABLED=false`.

3. **[mmsell · breakeven noise] All three books slightly positive (pooled ~+0.4¢/trade,
   n≈2,692), oscillating around zero.** Do NOT promote; keep as a zero-attention data book or
   prune for simplicity. No demonstrated edge net of realism.

4. **[weather_con · scale-up PAUSED — diagnose the drawdown] 3 of the last 6 entry-days sharply
   negative (8–14% win); cumulative +$1.46.** No new data overnight. Before any sizing, a fable
   pass on whether the losing days share a cause (city / window / forecast regime). Book stays
   on; diagnose-first, not a kill.

5. **[wcprop · kill rec stands] Zero trades ever.** `WCPROP_ENABLED=false` + optional one-shot
   `xmarket_wc` epitaph. Tournament ends in days.

6. **[XGAME · run the tape study — top info action] 19 matched games, multiple completed and on
   tape.** Run `xgame_tape_study` (fable/operator) to grade P1–P4; gate `xgame_book_enabled` on
   the result.

*(Changed this run: NEW #0 — stale crypto_ladder collector anomaly (verify/restart). theta (#1)
reinforced by the −$6.25 giveback (calm-streak reversion the calibration read predicted).
tfav (#2), mmsell (#3), weather_con (#4), wcprop (#5), XGAME (#6) unchanged — quiet overnight,
most books dormant. Data otherwise fresh; ladder is the lone red flag.)*
