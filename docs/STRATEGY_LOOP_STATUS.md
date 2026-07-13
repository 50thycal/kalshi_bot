# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-13 05:31 AM CDT (run #39)

**🔴 ANOMALY — THE BOT IS DOWN. Railway deployment status is CRASHED, crash-looping on a Kalshi
auth failure since ~9:31 PM CDT last night (~8 hours as of this run).** This supersedes every
book/gate item below — nothing has traded or collected data since ~9:16 PM CDT because the
process cannot get past startup.

**What's confirmed (read-only checks, ops channel):**
- Railway's own deployment record: `e0db4e4d-...` **status=CRASHED**, created **2026-07-13
  02:31:45 UTC** (9:31 PM CDT 7/12).
- Crash-loop error, repeating every ~3 seconds since then:
  `kalshi_bot.kalshi.errors.AuthError: Kalshi auth failed (401) on /trade-api/v2/portfolio/balance`
  — thrown from `main.py:175` (`client.get_balance()`), called unconditionally at startup before
  any trading or collection logic runs. Every restart attempt dies at the same line.
- `bot_runs`: last row started **02:16:40 UTC**, finished **02:16:52 UTC** — **zero runs since**
  (would be ~50+ runs by now at normal cadence). Confirms total halt, not a partial degradation.
- Every collector (`crypto_spot_candles`, `crypto_ladder_snapshots`, `weather_forecasts`,
  `weather_observations`, `weather_ensembles`, `weather_bucket_snapshots`) has its latest row
  clustered at **02:12–02:17 UTC** — all stopped in the same ~5-minute window, consistent with
  the crash. `xgame_matches` and `xgame_tapes` show **zero rows in the last 24h** (previously
  still ticking even though the book is shelved) — further confirming total collector halt, not
  an xgame-specific issue.

**Likely cause:** a 401 on the Kalshi balance endpoint at process start almost always means the
API key/private-key credential Railway is using stopped authenticating — expired, rotated, or
revoked on the Kalshi side, or a bad value got deployed. **This is outside what the loop can
diagnose further read-only** — it needs the actual credential checked (Kalshi account API-key
settings) and likely a Railway env var fix + redeploy. **This is a "flag now, don't wait for the
next scheduled run" situation** — surfacing at 5:30 AM instead of sitting until noon.

---

**Trading books (settled n / P&L / per-trade / open) — snapshot as of the crash (~02:16 UTC),
nothing has moved since:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell3 (5-10c) | 285 | +$4.24 | +1.5 | 24 | flat since crash (+3 trades, +8c/trade, right at the gate line) |
| pin15 | 133 | −$5.39 | −4.1 | 0 | flat since crash (+5 trades, this tiny batch +21.6c/trade — positive, but n too small to read) |
| mmsell1 / mmsell2 | 1,338 / 877 | +$17.34 / +$12.88 | +1.3 / +1.5 | 31 / 18 | mmsell3's lead over mmsell2 has nearly vanished (1.5 vs 1.5) |
| mmsell (control) | 2,262 | +$18.23 | +0.8 | 42 | flat since crash |
| weather_concity / theta4 / weather con(all) | 14 / 3 / 339 | unchanged | unchanged | unchanged | no new settles since run #38, now also frozen by the outage |
| theta ctrl/1/2/3, tfav, weather(rest) | — | — | — | — | SHELVED/KILLED, unaffected either way |

**Gate sweep:** mmsell3 **285/150** (~+1.5c, right on the line — see #38's note that this edge is
noise-comparable at this size) · pin15 **133/150** (89%, still −EV cum) · theta4 **3/80** ·
weather_concity **14/120**. All frozen mid-outage, nothing new to resolve this run.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** 🔴 Bot has been CRASHED/down since ~9:31 PM CDT last night (~8h), crash-looping on a
Kalshi 401 auth error at startup — likely an expired/rotated API key. All trading and data
collection halted since ~9:16 PM CDT. This needs a human to check the Kalshi API credential and
redeploy — the loop can't fix this. Below-the-fold: book P&L unchanged from #38 since everything
froze at the same moment; nothing to report there this run beyond "still frozen."

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[🔴 URGENT — bot down, needs human action NOW] Railway deployment CRASHED since ~9:31 PM CDT
   7/12, crash-looping on `AuthError: Kalshi auth failed (401) on /trade-api/v2/portfolio/balance`
   at startup.** `bot_runs` confirms zero runs since 02:16:52 UTC; every collector confirms the
   same halt window. **Action needed: check the Kalshi API key/credential Railway is using (most
   likely expired/rotated/revoked) and redeploy once fixed.** This is not something the loop or a
   future loop run can resolve — it needs direct operator or Railway-console intervention. Will
   re-check at the next scheduled run (12:00 PM CT) and escalate again if still down.

2. **[mmsell3 · still right at the +1.5c line, frozen mid-outage] +1.5c/trade at n=285** — last
   run's dip-below concern and this run's recovery-to-the-line are both within noise; nothing
   resolved either way, and no new data since the crash. Still: do NOT promote yet, wait for a
   clean multi-run read once the bot is back up and the edge has had a chance to stabilize.

3. **[idea-model queue · MMX — still "recheck before building," unaffected by the outage]**
   mmsell3's n≥150 trigger technically stands, but the performance leg is still unresolved (#2).
   MMX (`IDEA_MODEL_20260710_run2.md`) stays "recheck before building," not a green light. NEST
   still behind theta4 (n=3/80). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live
   Fed event.

4. **[pin15 · WATCH, frozen mid-outage] 133 settled −$5.39 (−4.1c cum), 89% to its n≥150 gate.**
   No new data since the crash; last partial batch (n=5) was positive but too small to read.
   Should resolve within a run or two of the bot coming back online.

5. **[weather_concity · WATCH, frozen mid-outage] 14 settled −$0.78, unchanged since #37/#38.**
   Gate n≥120 (12% there). No new data possible until the outage resolves.

6. **[theta4 · unchanged, frozen mid-outage] 3 trades, +$2.34.** Gate n≥80. No new data possible
   until the outage resolves.

7. **[mmsell existing · context] control/mmsell1/mmsell2 ~breakeven-positive** (+0.8c/+1.3c/+1.5c).
   mmsell3's lead over mmsell2 has essentially disappeared (1.5c vs 1.5c) — worth remembering once
   fresh data resumes, this comparison may flip either way.

8. **[data anomaly · xgame_tapes latest-timestamp — RESOLVED, explained by the outage] The
   repeating-stale-timestamp anomaly flagged in runs #37/#38 is now explained: the entire worker
   stopped at ~02:16 UTC (this run's #1 finding), so xgame_tapes freezing at the same moment is
   just the outage, not a separate query bug.** Drop as a standalone item; folded into #1.

*(Changed this run: NEW #1 — critical: bot CRASHED since ~9:31 PM CDT 7/12 on a Kalshi auth 401,
zero activity in ~8h, needs operator action. #2 mmsell3 — dip/recovery both within noise, frozen
mid-outage, still hold. #8 — the xgame_tapes anomaly from #37/#38 is now explained by the outage
and folded in, no longer a standalone mystery. #3-7 otherwise unchanged, all frozen since the
crash — nothing new to read until the bot is back up.)*
